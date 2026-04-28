"""
document_processor.py — Agent 2: OCR + LLM-based Document Extraction.

Responsibility:
  - Accept an uploaded document (image or PDF)
  - Extract structured fields relevant to the change_type using Gemini Vision
  - Run layered forgery detection:
      1. Deterministic code checks (magic bytes, PDF/EXIF metadata, file hygiene)
      2. Dedicated Gemini visual forgery assessment (separate call)
      3. Combine both into a final PASS/WARN/FAIL verdict with full signal list
  - Archive the document to mock FileNet and return a reference ID

Two modes:
  REAL MODE   — Uses Google Gemini multimodal API (requires GEMINI_API_KEY in .env)
  MOCK MODE   — Returns hardcoded-but-realistic extraction results so the prototype
                runs fully without any API key (USE_MOCK_LLM=True when key is blank)

Design note: Extraction and forgery assessment are deliberately split into two
Gemini calls. A single prompt asked to do both does each worse. The forgery call
gets a focused, adversarial prompt that asks for structured per-signal analysis
rather than a single PASS/FAIL verdict.
"""

import json
import re
from pathlib import Path

from app.config import settings
from app.services.filenet_mock import archive_document
from app.services.forgery_checks import run_code_forgery_checks
from app.services.observability import get_logger

logger = get_logger("document_processor")


# ── Forgery verdict combination policy ────────────────────────────────────────

_VERDICT_RANK = {"PASS": 0, "WARN": 1, "FAIL": 2}


def _combine_verdicts(*verdicts: str) -> str:
    """Return the most severe of the given verdicts (PASS < WARN < FAIL)."""
    worst = 0
    for v in verdicts:
        worst = max(worst, _VERDICT_RANK.get(str(v).upper(), 0))
    for label, rank in _VERDICT_RANK.items():
        if rank == worst:
            return label
    return "PASS"


# ── Prompt Templates ──────────────────────────────────────────────────────────

# Extraction prompt — focused solely on pulling fields out of the document.
# Forgery analysis is intentionally handled in a separate call (see FORGERY_PROMPT).
EXTRACTION_PROMPTS = {
    "LEGAL_NAME_CHANGE": """
You are a document verification AI for a bank. Analyse the provided document image and extract the following fields as a JSON object:

{
  "document_type_detected": "<type of document, MUST be one of: 'Marriage Certificate', 'Gazette Notification', 'Deed Poll', or 'Other/Screenshot'>",
  "bride_name": "<full name of the bride / previous name>",
  "married_name": "<full name after marriage / new name>",
  "issue_date": "<date the document was issued, YYYY-MM-DD or null>",
  "issuing_authority": "<name of issuing authority or null>",
  "document_number": "<certificate/reference number or null>",
  "is_legible": true or false,
  "extraction_confidence": "<HIGH, MEDIUM, or LOW>"
}

Return ONLY the JSON object. No explanation. If a field cannot be determined, use null.
""",
}


# Forgery-analysis prompt — a dedicated, adversarial second pass.
# The model is explicitly asked to look for tampering signals rather than to
# say "is this a document?". Output is a structured per-signal breakdown so
# the Checker UI can show the human *why* a request was flagged.
FORGERY_PROMPT = """
You are a forensic document examiner for a bank. The image you are analysing is
claimed to be an official supporting document for an account change request.

Your ONLY job is to assess whether this document shows tampering, fabrication,
or AI-generation indicators. Do NOT re-extract fields. Be sceptical by default —
if you are unsure, say so in the signal detail rather than defaulting to PASS.

Inspect for these signals and return a JSON object with this exact shape:

{
  "text_alignment_consistent":       {"ok": true|false, "confidence": 0-100, "detail": "<short>"},
  "font_consistency":                {"ok": true|false, "confidence": 0-100, "detail": "<short>"},
  "local_recompression_artifacts":   {"present": true|false, "confidence": 0-100, "detail": "<short>"},
  "seal_or_stamp_geometry_coherent": {"ok": true|false, "confidence": 0-100, "detail": "<short>"},
  "paper_texture_vs_flat_render":    {"value": "textured"|"flat"|"unclear", "confidence": 0-100, "detail": "<short>"},
  "compression_uniform_across_page": {"ok": true|false, "confidence": 0-100, "detail": "<short>"},
  "ai_generation_indicators":        {"present": true|false, "confidence": 0-100, "detail": "<short>"},
  "sample_or_specimen_watermark":    {"present": true|false, "detail": "<short>"},
  "overall_forgery_risk":            <integer 0-10, 0 = clean, 10 = definitely fake>,
  "overall_verdict":                 "PASS" | "WARN" | "FAIL",
  "reasoning":                       "<1-3 sentences summarising the strongest tamper signals>"
}

Scoring policy for overall_verdict (MUST follow):
- FAIL: clear evidence of tampering, a UI screenshot, AI-generated content, or a
  specimen/sample watermark on what is meant to be a real submission.
- WARN: one or more soft signals (inconsistent fonts, local recompression,
  unclear seal geometry) but not conclusive.
- PASS: no notable tamper signals across any of the above checks.

Return ONLY the JSON object. No extra text.
"""


# ── Mock extraction results ───────────────────────────────────────────────────

MOCK_EXTRACTIONS = {
    "LEGAL_NAME_CHANGE": {
        "document_type_detected": "Marriage Certificate",
        "bride_name": "Priya Sharma",
        "married_name": "Priya Mehta",
        "issue_date": "2023-11-20",
        "issuing_authority": "Municipal Corporation of Mumbai",
        "document_number": "MC/2023/112847",
        "is_legible": True,
        "extraction_confidence": "HIGH",
    },
}


# Mock forgery-analysis result used when running without a Gemini key.
MOCK_FORGERY_ANALYSIS = {
    "text_alignment_consistent":       {"ok": True,  "confidence": 85, "detail": "Uniform baselines across fields."},
    "font_consistency":                {"ok": True,  "confidence": 85, "detail": "Single font family throughout."},
    "local_recompression_artifacts":   {"present": False, "confidence": 80, "detail": "No localised re-compression detected."},
    "seal_or_stamp_geometry_coherent": {"ok": True,  "confidence": 80, "detail": "Seal appears geometrically consistent."},
    "paper_texture_vs_flat_render":    {"value": "textured", "confidence": 70, "detail": "Scan-like paper texture visible."},
    "compression_uniform_across_page": {"ok": True,  "confidence": 80, "detail": "Uniform JPEG compression across regions."},
    "ai_generation_indicators":        {"present": False, "confidence": 80, "detail": "No obvious AI-generation tells."},
    "sample_or_specimen_watermark":    {"present": False, "detail": "No watermark detected."},
    "overall_forgery_risk":            2,
    "overall_verdict":                 "PASS",
    "reasoning":                       "Mock-mode analysis: no tamper signals identified.",
}


# ── Gemini Real Extraction ────────────────────────────────────────────────────

def _extract_with_gemini(file_path: Path, change_type: str) -> dict:
    """
    Call Gemini multimodal API to extract document fields.
    Handles both image and PDF inputs.
    """
    import google.generativeai as genai
    from PIL import Image
    import PyPDF2

    genai.configure(api_key=settings.GEMINI_API_KEY)
    logger.info("GEMINI_MODEL_SELECTED", model=settings.GEMINI_MODEL)
    model = genai.GenerativeModel(settings.GEMINI_MODEL)

    prompt = EXTRACTION_PROMPTS.get(change_type)
    if prompt is None:
        # Defence in depth: intake already rejects unsupported change types,
        # but if one slips through, fail loudly rather than silently.
        raise ValueError(
            f"Unsupported change_type '{change_type}'. "
            f"Supported: {list(EXTRACTION_PROMPTS.keys())}."
        )

    # Prepare image input
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        # Convert first PDF page to image for vision processing
        from PIL import Image as PILImage
        import io
        reader = PyPDF2.PdfReader(str(file_path))
        # For mock: render as placeholder if PyMuPDF not installed
        # In production, use fitz (PyMuPDF) for accurate PDF→image conversion
        logger.warning("PDF_VISION_FALLBACK", note="Using text extraction fallback for PDF")
        pdf_text = "\n".join(
            reader.pages[i].extract_text() or ""
            for i in range(min(2, len(reader.pages)))
        )
        text_prompt = prompt + f"\n\nDocument text content:\n{pdf_text}"
        response = model.generate_content(text_prompt)
    else:
        image = Image.open(file_path)
        response = model.generate_content([prompt, image])

    raw_text = response.text.strip()

    # Strip markdown code fences if Gemini wraps in ```json ... ```
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
    raw_text = re.sub(r"\s*```$", "", raw_text)

    extracted = json.loads(raw_text)
    logger.info("GEMINI_EXTRACTION_SUCCESS", change_type=change_type, fields=list(extracted.keys()))
    return extracted


# ── Gemini Forgery Analysis (dedicated second call) ──────────────────────────

def _gemini_forgery_analysis(file_path: Path) -> dict:
    """
    Second, focused Gemini call whose ONLY job is tamper/forgery assessment.

    Keeping extraction and forgery analysis in separate calls produces better
    results than a single combined prompt — each call gets to focus on one job.

    Returns the structured forgery payload described in FORGERY_PROMPT. Raises
    on any failure; caller decides whether to retry, demote, or fail.
    """
    import google.generativeai as genai
    from PIL import Image
    import PyPDF2

    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(settings.GEMINI_MODEL)

    ext = file_path.suffix.lower()
    if ext == ".pdf":
        reader = PyPDF2.PdfReader(str(file_path))
        pdf_text = "\n".join(
            reader.pages[i].extract_text() or ""
            for i in range(min(2, len(reader.pages)))
        )
        text_prompt = FORGERY_PROMPT + f"\n\nDocument text content:\n{pdf_text}"
        response = model.generate_content(text_prompt)
    else:
        image = Image.open(file_path)
        response = model.generate_content([FORGERY_PROMPT, image])

    raw_text = response.text.strip()
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
    raw_text = re.sub(r"\s*```$", "", raw_text)

    payload = json.loads(raw_text)
    logger.info(
        "GEMINI_FORGERY_ANALYSIS_SUCCESS",
        verdict=payload.get("overall_verdict"),
        risk=payload.get("overall_forgery_risk"),
    )
    return payload


def _gemini_verdict_to_signals(forgery_payload: dict) -> list[dict]:
    """
    Turn the structured Gemini forgery payload into the same signal shape used
    by the code-level checks, so the scorer only has to reason about one list.
    """
    signals: list[dict] = []

    # Hard-negative signals first: specimen watermark, AI generation, screenshot
    watermark = forgery_payload.get("sample_or_specimen_watermark") or {}
    if watermark.get("present"):
        signals.append({
            "name": "gemini_specimen_or_sample_watermark",
            "severity": "critical",
            "detail": watermark.get("detail") or "Specimen/sample watermark detected.",
        })

    ai_gen = forgery_payload.get("ai_generation_indicators") or {}
    if ai_gen.get("present") and (ai_gen.get("confidence") or 0) >= 60:
        signals.append({
            "name": "gemini_ai_generation_indicators",
            "severity": "critical",
            "detail": ai_gen.get("detail") or "Possible AI-generated document.",
        })

    # Soft-negative signals: alignment, fonts, seal, compression
    alignment = forgery_payload.get("text_alignment_consistent") or {}
    if alignment.get("ok") is False:
        signals.append({
            "name": "gemini_text_alignment_inconsistent",
            "severity": "warn",
            "detail": alignment.get("detail") or "Inconsistent text alignment suggests tampering.",
        })

    fonts = forgery_payload.get("font_consistency") or {}
    if fonts.get("ok") is False:
        signals.append({
            "name": "gemini_font_inconsistency",
            "severity": "warn",
            "detail": fonts.get("detail") or "Mixed fonts detected within fields that should use one font.",
        })

    recompress = forgery_payload.get("local_recompression_artifacts") or {}
    if recompress.get("present") and (recompress.get("confidence") or 0) >= 50:
        signals.append({
            "name": "gemini_local_recompression",
            "severity": "warn",
            "detail": recompress.get("detail") or "Local recompression artefacts near critical fields.",
        })

    seal = forgery_payload.get("seal_or_stamp_geometry_coherent") or {}
    if seal.get("ok") is False:
        signals.append({
            "name": "gemini_seal_geometry_incoherent",
            "severity": "warn",
            "detail": seal.get("detail") or "Seal or stamp geometry appears inconsistent.",
        })

    compression = forgery_payload.get("compression_uniform_across_page") or {}
    if compression.get("ok") is False:
        signals.append({
            "name": "gemini_nonuniform_compression",
            "severity": "warn",
            "detail": compression.get("detail") or "Compression differs across page regions.",
        })

    paper = forgery_payload.get("paper_texture_vs_flat_render") or {}
    if paper.get("value") == "flat" and (paper.get("confidence") or 0) >= 60:
        signals.append({
            "name": "gemini_flat_render_no_paper_texture",
            "severity": "warn",
            "detail": paper.get("detail") or "Document appears digitally rendered rather than scanned.",
        })

    # Numeric risk threshold — if Gemini is confident in its overall verdict.
    risk = forgery_payload.get("overall_forgery_risk")
    try:
        risk_int = int(risk) if risk is not None else None
    except (TypeError, ValueError):
        risk_int = None
    if risk_int is not None and risk_int >= 8:
        signals.append({
            "name": "gemini_high_overall_risk",
            "severity": "critical",
            "detail": forgery_payload.get("reasoning") or f"Overall risk score {risk_int}/10.",
        })

    return signals


def _gemini_payload_to_verdict(forgery_payload: dict) -> str:
    """Return PASS/WARN/FAIL from the Gemini forgery payload (with fallback)."""
    v = str(forgery_payload.get("overall_verdict") or "").upper()
    if v in _VERDICT_RANK:
        return v
    risk = forgery_payload.get("overall_forgery_risk")
    try:
        risk_int = int(risk)
    except (TypeError, ValueError):
        return "WARN"
    if risk_int >= 7:
        return "FAIL"
    if risk_int >= 4:
        return "WARN"
    return "PASS"


# ── Main Entry Point ──────────────────────────────────────────────────────────

def process_document(
    file_path: Path,
    customer_id: str,
    change_type: str,
    document_type: str,
    request_id: str,
) -> dict:
    """
    Full document processing pipeline:
      1. Deterministic code-level forgery checks (magic bytes, metadata, hygiene)
      2. Gemini extraction of document fields
      3. Gemini dedicated forgery analysis
      4. Combine code + Gemini signals into a single verdict
      5. Archive to mock FileNet

    Returns:
        {
            "extracted_fields":  dict,
            "forgery_check":     "PASS" | "WARN" | "FAIL",
            "forgery_signals":   [ {name, severity, detail}, ... ],  # audit trail
            "forgery_reasoning": str,                                # Gemini summary
            "filenet_ref_id":    str,
            "mode":              "real" | "mock",
        }
    """
    logger.info(
        "DOCUMENT_PROCESSING_START",
        request_id=request_id,
        change_type=change_type,
        file=file_path.name,
        mode="mock" if settings.USE_MOCK_LLM else "real",
    )

    # ── Step 1: Deterministic code-level forgery checks ──────────────────────
    code_result = run_code_forgery_checks(file_path)
    code_verdict = code_result["verdict"]
    code_signals = code_result["signals"]

    # ── Step 2: Extract fields via Gemini (or mock) ───────────────────────────
    used_mode = "mock" if settings.USE_MOCK_LLM else "real"

    if settings.USE_MOCK_LLM:
        logger.info("LLM_MODE", mode="mock", reason="No GEMINI_API_KEY provided")
        extracted_fields = MOCK_EXTRACTIONS.get(
            change_type,
            {"note": "No mock extraction defined for this change_type", "extraction_confidence": "LOW"},
        )
    else:
        try:
            extracted_fields = _extract_with_gemini(file_path, change_type)
        except Exception as exc:
            logger.error("GEMINI_EXTRACTION_FAILED", error=str(exc))
            if settings.GEMINI_STRICT:
                raise
            # Graceful degradation: fall back to mock so processing continues
            used_mode = "mock"
            logger.warning("LLM_FALLBACK_TO_MOCK", reason=str(exc))
            extracted_fields = MOCK_EXTRACTIONS.get(change_type, {})
            extracted_fields["extraction_confidence"] = "LOW"

    # ── Step 3: Dedicated Gemini forgery analysis ────────────────────────────
    # A second, focused call purely for tamper/forgery assessment. This gives
    # markedly better results than asking the extraction prompt to also judge
    # authenticity. If it fails we demote to WARN rather than silently passing.
    forgery_payload: dict
    gemini_forgery_verdict: str
    gemini_signals: list[dict]

    if used_mode == "mock":
        forgery_payload = dict(MOCK_FORGERY_ANALYSIS)
        gemini_forgery_verdict = forgery_payload["overall_verdict"]
        gemini_signals = []  # mock is clean by design
    else:
        try:
            forgery_payload = _gemini_forgery_analysis(file_path)
            gemini_forgery_verdict = _gemini_payload_to_verdict(forgery_payload)
            gemini_signals = _gemini_verdict_to_signals(forgery_payload)
        except Exception as exc:
            logger.warning("GEMINI_FORGERY_ANALYSIS_FAILED", error=str(exc))
            if settings.GEMINI_STRICT:
                raise
            # Loss of a signal is itself a signal — demote to WARN so the
            # Checker knows they are working with incomplete information.
            forgery_payload = {
                "overall_verdict": "WARN",
                "overall_forgery_risk": None,
                "reasoning": f"Gemini forgery analysis unavailable: {exc}",
            }
            gemini_forgery_verdict = "WARN"
            gemini_signals = [{
                "name": "gemini_forgery_analysis_unavailable",
                "severity": "warn",
                "detail": f"Forgery analysis call failed: {exc}",
            }]

    # ── Step 4: Combine code + Gemini signals into a final verdict ───────────
    all_signals = code_signals + gemini_signals
    final_verdict = _combine_verdicts(code_verdict, gemini_forgery_verdict)

    logger.info(
        "FORGERY_VERDICT",
        final_verdict=final_verdict,
        code_verdict=code_verdict,
        gemini_verdict=gemini_forgery_verdict,
        signal_count=len(all_signals),
        signals=[{"name": s["name"], "severity": s["severity"]} for s in all_signals],
    )

    forgery_reasoning = str(forgery_payload.get("reasoning") or "").strip()

    # ── Step 5: Archive to mock FileNet ───────────────────────────────────────
    filenet_result = archive_document(
        source_path=file_path,
        customer_id=customer_id,
        change_type=change_type,
        document_type=document_type,
        request_id=request_id,
    )

    logger.info(
        "DOCUMENT_PROCESSING_COMPLETE",
        request_id=request_id,
        filenet_ref=filenet_result["ref_id"],
        forgery=final_verdict,
    )

    return {
        "extracted_fields":  extracted_fields,
        "forgery_check":     final_verdict,
        "forgery_signals":   all_signals,
        "forgery_reasoning": forgery_reasoning,
        "filenet_ref_id":    filenet_result["ref_id"],
        "mode":              used_mode,
    }
