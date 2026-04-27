"""
document_processor.py — Agent 2: OCR + LLM-based Document Extraction.

Responsibility:
  - Accept an uploaded document (image or PDF)
  - Extract structured fields relevant to the change_type using Gemini Vision
  - Run basic forgery-detection heuristics (metadata checks, file integrity)
  - Archive the document to mock FileNet and return a reference ID

Two modes:
  REAL MODE   — Uses Google Gemini 1.5 Flash multimodal API (requires GEMINI_API_KEY in .env)
  MOCK MODE   — Returns hardcoded-but-realistic extraction results so the prototype
                runs fully without any API key (USE_MOCK_LLM=True when key is blank)

Design note: We use Gemini's vision capability to process the document image in a
single API call rather than a separate OCR step followed by an NLP step. This reduces
latency, cost, and the error surface of a two-step pipeline.
"""

import json
import re
from pathlib import Path
from datetime import datetime

from app.config import settings
from app.services.filenet_mock import archive_document
from app.services.observability import get_logger

logger = get_logger("document_processor")


# ── Forgery Detection Heuristics ──────────────────────────────────────────────

def _run_forgery_heuristics(file_path: Path) -> dict:
    """
    Lightweight forgery-detection checks that don't require an LLM.

    Checks performed:
      - File exists and is non-empty (basic integrity)
      - File extension matches expected document MIME types
      - File size is within plausible bounds (< 20 MB)
      - No suspicious double-extension patterns (e.g. .pdf.exe)

    Returns dict with 'result' (PASS/FAIL/WARN) and 'findings' list.
    In production, extend with: EXIF metadata analysis, DPI validation,
    font consistency checks via CV, and hash verification against known fakes.
    """
    findings = []
    allowed_extensions = {".jpg", ".jpeg", ".png", ".pdf", ".tiff"}
    ext = file_path.suffix.lower()

    if not file_path.exists() or file_path.stat().st_size == 0:
        return {"result": "FAIL", "findings": ["File is empty or missing."]}

    if ext not in allowed_extensions:
        findings.append(f"Unexpected file extension: {ext}")

    size_mb = file_path.stat().st_size / (1024 * 1024)
    if size_mb > 20:
        findings.append(f"File size {size_mb:.1f} MB exceeds 20 MB limit.")

    # Double-extension attack pattern
    if len(file_path.suffixes) > 1:
        findings.append(f"Suspicious double extension detected: {''.join(file_path.suffixes)}")

    if findings:
        return {"result": "WARN", "findings": findings}
    return {"result": "PASS", "findings": ["No anomalies detected."]}


# ── Prompt Templates ──────────────────────────────────────────────────────────

EXTRACTION_PROMPTS = {
    "LEGAL_NAME_CHANGE": """
You are a document verification AI for a bank. Analyse the provided document image and extract the following fields as a JSON object:

{
  "document_type_detected": "<type of document you see, MUST be one of: 'Marriage Certificate', 'Gazette Notification', 'Deed Poll', or 'Other/Screenshot'>",
  "bride_name": "<full name of the bride / previous name>",
  "married_name": "<full name after marriage / new name>",
  "issue_date": "<date the document was issued, YYYY-MM-DD or null>",
  "issuing_authority": "<name of issuing authority or null>",
  "document_number": "<certificate/reference number or null>",
  "is_legible": true or false,
  "extraction_confidence": "<HIGH, MEDIUM, or LOW>",
  "forgery_analysis": "<Briefly explain if this looks like a scanned/photographed document. Ignore 'sample' or 'specimen' watermarks.>",
  "forgery_status": "<PASS if it is a document image, FAIL ONLY if it is clearly a UI screenshot or completely unrelated image.>"
}

Return ONLY the JSON object. No explanation. If a field cannot be determined, use null.
""",
    "ADDRESS_CHANGE": """
You are a document verification AI for a bank. Analyse this document and extract:

{
  "document_type_detected": "<Utility Bill, Bank Statement, Lease Agreement, etc.>",
  "account_holder_name": "<name on the document>",
  "address_line_1": "<street address>",
  "city": "<city>",
  "state": "<state or region>",
  "pincode": "<postal code>",
  "issue_date": "<date on the document, YYYY-MM-DD or null>",
  "is_legible": true or false,
  "extraction_confidence": "<HIGH, MEDIUM, or LOW>"
}

Return ONLY the JSON object.
""",
}

# Default prompt for unsupported change types
DEFAULT_PROMPT = """
Analyse this document and extract all clearly legible text fields as a JSON object.
Return ONLY the JSON object with field names as keys and extracted text as values.
Include an 'extraction_confidence' field: HIGH, MEDIUM, or LOW.
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
    "ADDRESS_CHANGE": {
        "document_type_detected": "Utility Bill",
        "account_holder_name": "Rahul Verma",
        "address_line_1": "88 Koramangala 4th Block",
        "city": "Bengaluru",
        "state": "Karnataka",
        "pincode": "560034",
        "issue_date": "2024-03-01",
        "is_legible": True,
        "extraction_confidence": "HIGH",
    },
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

    prompt = EXTRACTION_PROMPTS.get(change_type, DEFAULT_PROMPT)

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
      1. Forgery heuristics
      2. OCR / LLM extraction (Gemini or mock)
      3. Archive to mock FileNet
      4. Return structured result

    Returns:
        {
            "extracted_fields": dict,
            "forgery_check":    "PASS" | "FAIL" | "WARN",
            "filenet_ref_id":   str,
            "mode":             "real" | "mock",
        }
    """
    logger.info(
        "DOCUMENT_PROCESSING_START",
        request_id=request_id,
        change_type=change_type,
        file=file_path.name,
        mode="mock" if settings.USE_MOCK_LLM else "real",
    )

    # ── Step 1: Forgery heuristics ────────────────────────────────────────────
    forgery_result = _run_forgery_heuristics(file_path)
    logger.info(
        "FORGERY_CHECK",
        result=forgery_result["result"],
        findings=forgery_result["findings"],
    )

    # ── Step 2: Extract fields ────────────────────────────────────────────────
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

    # Merge Gemini forgery analysis into final forgery result
    if used_mode == "real" and "forgery_status" in extracted_fields:
        if extracted_fields["forgery_status"] == "FAIL":
            forgery_result["result"] = "FAIL"
            forgery_result["findings"].append(extracted_fields.get("forgery_analysis", "AI vision failed authenticity check."))

    # ── Step 3: Archive to mock FileNet ───────────────────────────────────────
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
        forgery=forgery_result["result"],
    )

    return {
        "extracted_fields": extracted_fields,
        "forgery_check":    forgery_result["result"],
        "filenet_ref_id":   filenet_result["ref_id"],
        "mode":             used_mode,
    }
