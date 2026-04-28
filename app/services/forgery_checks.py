"""
forgery_checks.py — Deterministic forgery-signal checks for uploaded documents.

This module runs entirely in-code (no LLM calls). It produces a structured set
of signals that feed into the final forgery verdict alongside Gemini's visual
assessment.

Signals are returned as a list of dicts:
    {
        "name":     "<short signal id, e.g. 'pdf_edited_after_creation'>",
        "severity": "info" | "warn" | "critical",
        "detail":   "<human-readable explanation>",
    }

A top-level aggregator combines all signals into a verdict:
    PASS  — zero signals
    WARN  — 1+ 'warn' signals
    FAIL  — 1+ 'critical' signals (overrides WARN)

Why this module exists:
    The previous implementation labelled basic file-hygiene checks as "forgery
    detection". This module does the real work: PDF metadata inspection, EXIF
    inspection, magic-byte validation, and filename hygiene. Gemini's visual
    signals are layered on top in document_processor.py.

Not yet implemented (intentional, called out for future work):
    - Perceptual hashing / duplicate detection across requests
    - Error Level Analysis (ELA) for JPEG tamper localisation
    - Dedicated AI-generation detectors (DALL-E / Midjourney output)
    - Issuing-authority / document-number database cross-checks
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.services.observability import get_logger

logger = get_logger("forgery_checks")


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Magic-byte signatures for the file types we accept.
# Only the minimum bytes needed to uniquely identify the type.
_MAGIC_BYTES: dict[str, tuple[bytes, ...]] = {
    ".pdf":  (b"%PDF-",),
    ".jpg":  (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png":  (b"\x89PNG\r\n\x1a\n",),
    ".tiff": (b"II*\x00", b"MM\x00*"),
    ".tif":  (b"II*\x00", b"MM\x00*"),
}

_ALLOWED_EXTENSIONS = set(_MAGIC_BYTES.keys())

# PDF "Producer" substrings that indicate the document was edited rather than
# scanned/issued natively. Not all are forgeries — but for a document that is
# meant to be an official scan, any of these is a red flag.
_SUSPICIOUS_PDF_PRODUCERS = (
    "acrobat pro",
    "foxit editor",
    "foxit phantom",
    "illustrator",
    "photoshop",
    "pdfescape",
    "sejda",
    "smallpdf",
    "ilovepdf",
    "itextsharp",
)

# Cameras / phone producers in EXIF — expected for a photographed-on-phone
# proof of address but unusual for an "official document scan".
_PHONE_CAMERA_KEYWORDS = (
    "iphone", "samsung", "pixel", "oneplus", "xiaomi", "redmi", "oppo",
    "vivo", "realme", "huawei",
)


# ─────────────────────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────────────────────

def _check_file_hygiene(file_path: Path) -> list[dict[str, Any]]:
    """Basic upload sanity: file exists, non-empty, under 20 MB, no double ext."""
    signals: list[dict[str, Any]] = []

    if not file_path.exists() or file_path.stat().st_size == 0:
        signals.append({
            "name": "file_empty_or_missing",
            "severity": "critical",
            "detail": "Uploaded file is empty or cannot be read.",
        })
        return signals  # no point running other checks

    size_mb = file_path.stat().st_size / (1024 * 1024)
    if size_mb > 20:
        signals.append({
            "name": "file_exceeds_size_limit",
            "severity": "critical",
            "detail": f"File size {size_mb:.1f} MB exceeds 20 MB limit.",
        })

    # Unexpectedly tiny files (< 20 KB) for supposed document scans — usually
    # re-compressed fakes or screenshots saved at low quality.
    if 0 < size_mb < 0.02:
        signals.append({
            "name": "file_suspiciously_small",
            "severity": "warn",
            "detail": (
                f"File size {size_mb*1024:.0f} KB is unusually small for a "
                f"document scan. Possible re-compression or low-quality fake."
            ),
        })

    # Double-extension pattern (invoice.pdf.exe, scan.jpg.php)
    if len(file_path.suffixes) > 1:
        signals.append({
            "name": "double_extension",
            "severity": "critical",
            "detail": f"Suspicious double extension: {''.join(file_path.suffixes)}",
        })

    ext = file_path.suffix.lower()
    if ext and ext not in _ALLOWED_EXTENSIONS:
        signals.append({
            "name": "unexpected_extension",
            "severity": "warn",
            "detail": f"Unexpected file extension: {ext}",
        })

    return signals


def _check_magic_bytes(file_path: Path) -> list[dict[str, Any]]:
    """Verify the file's actual content matches its claimed extension."""
    signals: list[dict[str, Any]] = []
    ext = file_path.suffix.lower()

    expected = _MAGIC_BYTES.get(ext)
    if not expected:
        return signals  # extension itself is unexpected, already flagged above

    try:
        with open(file_path, "rb") as fh:
            header = fh.read(16)
    except OSError as exc:
        signals.append({
            "name": "file_unreadable",
            "severity": "critical",
            "detail": f"Could not read file header: {exc}",
        })
        return signals

    if not any(header.startswith(sig) for sig in expected):
        signals.append({
            "name": "content_type_mismatch",
            "severity": "warn",   # downgraded: phone photos shared via WhatsApp/email
                                  # are often re-encoded but still valid documents;
                                  # Gemini can analyse them regardless.
                                  # Keep as warn (not critical) to surface in audit
                                  # trail without auto-failing real submissions.
            "detail": (
                f"File extension '{ext}' does not match expected magic bytes. "
                f"This may indicate re-encoding (e.g. WhatsApp compression, "
                f"HEIC→JPEG conversion) rather than deliberate spoofing. "
                f"Review the uploaded file carefully."
            ),
        })

    return signals


def _check_pdf_metadata(file_path: Path) -> list[dict[str, Any]]:
    """Inspect PDF metadata for tamper indicators."""
    signals: list[dict[str, Any]] = []

    if file_path.suffix.lower() != ".pdf":
        return signals

    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(str(file_path))
        meta = reader.metadata or {}
    except Exception as exc:
        # Unreadable metadata is a soft signal — could be a corrupt-but-real PDF,
        # a minimal/old PDF, or a tamper indicator. Record it for the audit
        # trail but don't on its own escalate the verdict.
        signals.append({
            "name": "pdf_metadata_unreadable",
            "severity": "info",
            "detail": f"Could not read PDF metadata: {exc}",
        })
        return signals

    producer = (meta.get("/Producer") or "").lower()
    creator = (meta.get("/Creator") or "").lower()
    combined = f"{producer} {creator}"

    for bad in _SUSPICIOUS_PDF_PRODUCERS:
        if bad in combined:
            signals.append({
                "name": "pdf_edited_by_suspicious_software",
                "severity": "warn",
                "detail": (
                    f"PDF was last touched by editing software "
                    f"('{bad}' detected in Producer/Creator metadata). "
                    f"Official scanned documents should not carry editor metadata."
                ),
            })
            break

    creation_date = _parse_pdf_date(meta.get("/CreationDate"))
    mod_date = _parse_pdf_date(meta.get("/ModDate"))

    if creation_date and mod_date:
        # ModDate BEFORE CreationDate is impossible for an honest document.
        if mod_date < creation_date - timedelta(minutes=1):
            signals.append({
                "name": "pdf_modified_before_created",
                "severity": "critical",
                "detail": (
                    f"PDF ModDate ({mod_date.isoformat()}) is earlier than "
                    f"CreationDate ({creation_date.isoformat()}). "
                    f"This is impossible for a genuine document."
                ),
            })
        # ModDate significantly after CreationDate means the PDF was edited.
        elif mod_date > creation_date + timedelta(days=1):
            signals.append({
                "name": "pdf_edited_after_creation",
                "severity": "warn",
                "detail": (
                    f"PDF was modified {(mod_date - creation_date).days} days "
                    f"after creation. Expected behaviour for a re-saved edit."
                ),
            })

    now = datetime.utcnow()
    if creation_date and creation_date > now + timedelta(hours=1):
        signals.append({
            "name": "pdf_future_creation_date",
            "severity": "critical",
            "detail": (
                f"PDF CreationDate ({creation_date.isoformat()}) is in the "
                f"future. Impossible for a genuine document."
            ),
        })

    return signals


def _check_image_metadata(file_path: Path) -> list[dict[str, Any]]:
    """Inspect image EXIF for tamper/origin indicators."""
    signals: list[dict[str, Any]] = []

    if file_path.suffix.lower() not in {".jpg", ".jpeg", ".tiff", ".tif", ".png"}:
        return signals

    try:
        from PIL import Image, ExifTags
        img = Image.open(file_path)
        exif_raw = img.getexif() if hasattr(img, "getexif") else None
    except Exception as exc:
        # Unreadable image metadata is a soft signal. Record for audit but
        # don't on its own trip WARN — keeps false positives low for minimal
        # or re-saved images that are otherwise fine.
        signals.append({
            "name": "image_metadata_unreadable",
            "severity": "info",
            "detail": f"Could not read image metadata: {exc}",
        })
        return signals

    if not exif_raw:
        # No EXIF at all is common for scans + screenshots — not suspicious
        # by itself, but worth noting in the audit trail.
        signals.append({
            "name": "image_no_exif",
            "severity": "info",
            "detail": "Image has no EXIF metadata (common for scans/screenshots).",
        })
        return signals

    exif = {ExifTags.TAGS.get(k, k): v for k, v in exif_raw.items()}

    # Phone-camera origin on what should be an official document
    make = str(exif.get("Make") or "").lower()
    model = str(exif.get("Model") or "").lower()
    device_str = f"{make} {model}"
    for kw in _PHONE_CAMERA_KEYWORDS:
        if kw in device_str:
            signals.append({
                "name": "image_phone_camera_origin",
                "severity": "info",
                "detail": (
                    f"Image was taken on a phone camera ({make} {model}). "
                    f"Acceptable for a photographed document, but an "
                    f"official-looking scan with phone EXIF is unusual."
                ),
            })
            break

    # Software-edit detection
    software = str(exif.get("Software") or "").lower()
    edit_keywords = ("photoshop", "gimp", "lightroom", "snapseed", "pixlr",
                     "affinity photo", "illustrator")
    for kw in edit_keywords:
        if kw in software:
            signals.append({
                "name": "image_edited_in_software",
                "severity": "warn",
                "detail": (
                    f"Image was last saved by editing software ('{kw}' in "
                    f"EXIF:Software). Documents should not carry editor "
                    f"metadata."
                ),
            })
            break

    # Timestamp sanity — future-dated photos are impossible
    for tag in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
        stamp = _parse_exif_date(exif.get(tag))
        if stamp and stamp > datetime.utcnow() + timedelta(hours=1):
            signals.append({
                "name": "image_future_timestamp",
                "severity": "critical",
                "detail": (
                    f"Image EXIF {tag} is in the future ({stamp.isoformat()})."
                ),
            })
            break

    return signals


# ─────────────────────────────────────────────────────────────────────────────
# Date parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

_PDF_DATE_RE = re.compile(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?")


def _parse_pdf_date(raw: Any) -> datetime | None:
    """Parse a PDF date string like 'D:20240115120530+05'30''."""
    if not raw:
        return None
    s = str(raw)
    m = _PDF_DATE_RE.match(s)
    if not m:
        return None
    try:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4) or 0)
        minute = int(m.group(5) or 0)
        second = int(m.group(6) or 0)
        return datetime(year, month, day, hour, minute, second)
    except (TypeError, ValueError):
        return None


def _parse_exif_date(raw: Any) -> datetime | None:
    """Parse EXIF date 'YYYY:MM:DD HH:MM:SS'."""
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Aggregator — the public entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_code_forgery_checks(file_path: Path) -> dict[str, Any]:
    """
    Run all deterministic forgery checks and return a structured result.

    Returns:
        {
            "verdict":  "PASS" | "WARN" | "FAIL",
            "signals":  [ {name, severity, detail}, ... ],
        }
    """
    signals: list[dict[str, Any]] = []

    signals.extend(_check_file_hygiene(file_path))

    # If the file is unreadable, further checks are meaningless.
    has_critical_hygiene = any(
        s["name"] in {"file_empty_or_missing", "file_exceeds_size_limit"}
        for s in signals
    )
    if not has_critical_hygiene:
        signals.extend(_check_magic_bytes(file_path))
        signals.extend(_check_pdf_metadata(file_path))
        signals.extend(_check_image_metadata(file_path))

    severities = {s["severity"] for s in signals}
    if "critical" in severities:
        verdict = "FAIL"
    elif "warn" in severities:
        verdict = "WARN"
    else:
        verdict = "PASS"

    logger.info(
        "CODE_FORGERY_CHECKS_COMPLETE",
        verdict=verdict,
        signal_count=len(signals),
        signals=[{"name": s["name"], "severity": s["severity"]} for s in signals],
    )

    return {"verdict": verdict, "signals": signals}
