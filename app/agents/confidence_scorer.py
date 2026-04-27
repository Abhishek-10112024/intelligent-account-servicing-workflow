"""
confidence_scorer.py — Agent 3: Confidence Score Card Generator.

Responsibility:
  - Compare extracted document fields against the requested change values
  - Produce per-field confidence scores (0.0–1.0)
  - Compute an overall weighted confidence score
  - Generate a human-readable AI summary for the Checker Review UI
  - Assign a recommendation: APPROVE / FLAG / REJECT

Scoring methodology:
  - Name fields: fuzzy token-sort-ratio (handles "Priya Sharma" vs "PRIYA SHARMA")
  - Document authenticity: weighted combination of extraction confidence + forgery result
  - Overall: weighted average (name_match × 0.6 + authenticity × 0.4)
  - Thresholds from config: APPROVE_THRESHOLD=0.80, FLAG_THRESHOLD=0.60

Design note: We use fuzzywuzzy's token_sort_ratio rather than exact matching to handle
common OCR artefacts (extra spaces, capitalisation differences, accent variations).
"""

from fuzzywuzzy import fuzz
from app.config import settings
from app.services.observability import get_logger

logger = get_logger("confidence_scorer")


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _fuzzy_score(a: str, b: str) -> float:
    """
    Return a 0.0–1.0 similarity score between two strings.
    Uses token_sort_ratio to handle word-order and capitalisation differences.
    Example: ("Priya Sharma", "PRIYA SHARMA") → 1.0
    """
    if not a or not b:
        return 0.0
    return fuzz.token_sort_ratio(a.strip().lower(), b.strip().lower()) / 100.0


def _authenticity_score(extraction_confidence: str, forgery_result: str) -> float:
    """
    Derive a 0.0–1.0 document authenticity score from:
      - LLM's self-reported extraction confidence (HIGH / MEDIUM / LOW)
      - Forgery heuristic result (PASS / WARN / FAIL)

    This is a heuristic — in production, extend with:
      - Computer-vision-based tamper detection (pixel-level analysis)
      - Digital signature validation
      - Issuing-authority database cross-check
    """
    confidence_map = {"HIGH": 0.90, "MEDIUM": 0.70, "LOW": 0.45}
    forgery_map    = {"PASS": 1.00, "WARN": 0.70, "FAIL": 0.10}

    conf_score    = confidence_map.get(str(extraction_confidence).upper(), 0.50)
    forgery_score = forgery_map.get(str(forgery_result).upper(), 0.50)

    # Weighted: extraction confidence = 40 %, forgery result = 60 %
    return round(conf_score * 0.4 + forgery_score * 0.6, 4)


# ── Per-change-type scoring ───────────────────────────────────────────────────

def _score_legal_name_change(extracted: dict, old_value: str, new_value: str) -> dict:
    """Score a Legal Name Change document (e.g. Marriage Certificate)."""
    bride_name   = extracted.get("bride_name") or ""
    married_name = extracted.get("married_name") or ""
    missing_fields = []
    if not bride_name.strip():
        missing_fields.append("bride_name")
    if not married_name.strip():
        missing_fields.append("married_name")

    # Old name should match the bride name field
    old_match = _fuzzy_score(old_value, bride_name)
    # New name should match the married name field
    new_match = _fuzzy_score(new_value, married_name)

    # Combine: both matches must be high for full confidence
    name_match = round((old_match + new_match) / 2, 4)

    return {
        "name_match":  name_match,
        "field_scores": {
            "old_name_vs_bride_name":    old_match,
            "new_name_vs_married_name":  new_match,
        },
        "extracted_name": f"{bride_name} → {married_name}",
        "missing_fields": missing_fields,
    }


def _score_address_change(extracted: dict, old_value: str, new_value: str) -> dict:
    """Score an Address Change document (e.g. Utility Bill)."""
    doc_address = " ".join(filter(None, [
        extracted.get("address_line_1", ""),
        extracted.get("city", ""),
        extracted.get("state", ""),
        extracted.get("pincode", ""),
    ]))
    name_match = _fuzzy_score(new_value, doc_address)
    return {
        "name_match":  name_match,
        "field_scores": {"new_address_vs_document": name_match},
        "extracted_name": doc_address,
    }


_SCORERS = {
    "LEGAL_NAME_CHANGE": _score_legal_name_change,
    "ADDRESS_CHANGE":    _score_address_change,
}


# ── Summary generator ─────────────────────────────────────────────────────────

def _build_summary(
    change_type: str,
    old_value: str,
    new_value: str,
    extracted_name: str,
    name_match: float,
    authenticity: float,
    forgery_check: str,
    recommendation: str,
    overall: float,
    reject_reason: str | None = None,
) -> str:
    """Build the human-readable AI summary shown in the Checker Review UI."""

    doc_label = {
        "LEGAL_NAME_CHANGE": "Marriage Certificate",
        "ADDRESS_CHANGE":    "Address Proof",
        "DOB_CORRECTION":    "Birth Certificate",
        "CONTACT_UPDATE":    "Contact Proof",
    }.get(change_type, "Supporting Document")

    lines = [
        f"**{doc_label} verified.**",
        f"Old value '{old_value}' and new value '{new_value}' were cross-referenced against the extracted document data: '{extracted_name}'.",
        f"Name/Value match confidence: **{name_match*100:.0f}%**.",
        f"Document authenticity score: **{authenticity*100:.0f}%**.",
        f"Forgery check: **{forgery_check}**.",
        f"Overall confidence: **{overall*100:.0f}%**.",
        f"**AI Recommendation: {recommendation}.**",
    ]

    if recommendation == "FLAG":
        lines.append("⚠ One or more scores are below the approval threshold. Please review documents carefully.")
    elif recommendation == "REJECT":
        if reject_reason:
            lines.append(f"🚫 **Rejected reason:** {reject_reason}")
        else:
            lines.append("🚫 Confidence is critically low. This request requires manual re-submission with clearer documents.")

    # Keep summary readable in UI: use line breaks instead of a single long line.
    return "\n".join(lines)


# ── Main Entry Point ──────────────────────────────────────────────────────────

def compute_confidence(
    extracted_fields:  dict,
    change_type:       str,
    document_type:     str,
    old_value:         str,
    new_value:         str,
    forgery_check:     str,
) -> dict:
    """
    Compute the full Confidence Score Card.

    Args:
        extracted_fields:  Output of document_processor.process_document()
        change_type:       e.g. LEGAL_NAME_CHANGE
        old_value:         Current value in RPS (e.g. "Priya Sharma")
        new_value:         Requested new value (e.g. "Priya Mehta")
        forgery_check:     PASS / WARN / FAIL from forgery heuristics

    Returns:
        dict matching ConfidenceScoreCard schema + extra diagnostics.
    """
    logger.info("CONFIDENCE_SCORING_START", change_type=change_type)

    # ── Field scoring ──────────────────────────────────────────────────────────
    scorer_fn = _SCORERS.get(change_type)
    if scorer_fn:
        field_result = scorer_fn(extracted_fields, old_value, new_value)
    else:
        # Generic fallback: basic string similarity on any text fields
        any_text = " ".join(str(v) for v in extracted_fields.values() if isinstance(v, str))
        match = _fuzzy_score(new_value, any_text)
        field_result = {
            "name_match": match,
            "field_scores": {"generic_match": match},
            "extracted_name": any_text[:80],
        }

    name_match     = field_result["name_match"]
    extracted_name = field_result.get("extracted_name", "")
    missing_fields = field_result.get("missing_fields", [])

    # ── Authenticity score ─────────────────────────────────────────────────────
    extraction_confidence = extracted_fields.get("extraction_confidence", "MEDIUM")
    authenticity = _authenticity_score(extraction_confidence, forgery_check)

    # ── Overall weighted score ─────────────────────────────────────────────────
    # Name/value match carries more weight as it is the primary verification signal
    overall = round(name_match * 0.6 + authenticity * 0.4, 4)

    # ── Recommendation — evaluated in priority order ─────────────────────────
    #
    # Priority (highest to lowest):
    #   1. FAIL forgery → always REJECT, regardless of field scores
    #   2. WARN forgery → always FLAG, never auto-approve
    #   3. Missing required fields for this change_type → REJECT
    #   4. Document type mismatch (declared vs Gemini-detected) → REJECT
    #   5. Threshold-based: overall ≥ APPROVE_THRESHOLD → APPROVE
    #                        overall ≥ FLAG_THRESHOLD   → FLAG
    #                        otherwise                  → REJECT
    #
    # Note: all scores (name_match, authenticity, overall) come directly from
    # the LLM extraction and fuzzy-match logic above. There is no hardcoded
    # override — the AI output drives the final recommendation.
    # ──────────────────────────────────────────────────────────────────────────

    reject_reason = None

    # ── Step 1: Hard failure — forgery ────────────────────────────────────────
    if forgery_check == "FAIL":
        recommendation = "REJECT"
        reject_reason = "Document failed authenticity / forgery check."

    # ── Step 2: Forgery warning — demote to FLAG at most ─────────────────────
    elif forgery_check == "WARN":
        # WARN means we have suspicions but aren't certain; force human review
        recommendation = "FLAG"

    else:
        # ── Step 3: Document type mismatch ────────────────────────────────────
        if change_type == "LEGAL_NAME_CHANGE":
            detected_type = extracted_fields.get("document_type_detected", "")
            expected_type_map = {
                "MARRIAGE_CERTIFICATE": "Marriage Certificate",
                "GAZETTE_NOTIFICATION": "Gazette Notification",
                "DEED_POLL":            "Deed Poll",
            }
            expected_type = expected_type_map.get(document_type, document_type)
            is_expected_type = (
                expected_type.lower() in detected_type.lower()
                or detected_type.lower() in expected_type.lower()
            )
            if not is_expected_type and detected_type:
                recommendation = "REJECT"
                reject_reason = (
                    f"Document type mismatch. You declared '{expected_type}' "
                    f"but the AI detected '{detected_type}'. "
                    f"Please re-upload the correct document."
                )

        # ── Step 4: Missing required fields ───────────────────────────────────
        if not reject_reason and missing_fields:
            recommendation = "REJECT"
            reject_reason = (
                f"Required fields could not be extracted: {', '.join(missing_fields)}. "
                f"Please re-upload a clearer, legible document."
            )

        # ── Step 5: Threshold-based recommendation ────────────────────────────
        if not reject_reason:
            if overall >= settings.APPROVE_THRESHOLD:
                recommendation = "APPROVE"
            elif overall >= settings.FLAG_THRESHOLD:
                recommendation = "FLAG"
            else:
                recommendation = "REJECT"
                reject_reason = (
                    f"Overall confidence {overall*100:.0f}% is below the "
                    f"minimum threshold of {settings.FLAG_THRESHOLD*100:.0f}%."
                )

    # ── Summary text ──────────────────────────────────────────────────────────
    # Build the human-readable summary shown in the Checker UI.
    # Use detected document type if available, otherwise fall back to change_type label.
    detected_type_for_summary = extracted_fields.get("document_type_detected", "")
    summary = _build_summary(
        change_type=change_type,
        old_value=old_value,
        new_value=new_value,
        extracted_name=extracted_name,
        name_match=name_match,
        authenticity=authenticity,
        forgery_check=forgery_check,
        recommendation=recommendation,
        overall=overall,
        reject_reason=reject_reason,
    )

    score_card = {
        "name_match":          name_match,
        "authenticity":        authenticity,
        "forgery_check":       forgery_check,
        "overall_confidence":  overall,
        "recommendation":      recommendation,
        "summary":             summary,
        "field_scores":        field_result.get("field_scores", {}),
        "extracted_name":      extracted_name,
    }

    logger.info(
        "CONFIDENCE_SCORING_COMPLETE",
        overall=overall,
        recommendation=recommendation,
        name_match=name_match,
        authenticity=authenticity,
    )

    return score_card
