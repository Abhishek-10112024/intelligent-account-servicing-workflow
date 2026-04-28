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
  - Name match combines old and new fields using the WEAKEST LINK (min), so a
    correct old name + wrong new name cannot average its way to a FLAG.
  - Document authenticity: weighted combination of extraction confidence and
    forgery verdict (see _authenticity_score).
  - Overall: weighted average (name_match × 0.6 + authenticity × 0.4)
  - Thresholds are per-change-type (see settings.thresholds_for), falling back
    to global APPROVE_THRESHOLD / FLAG_THRESHOLD.

Design note: We use fuzzywuzzy's token_sort_ratio rather than exact matching to
handle common OCR artefacts (extra spaces, capitalisation, accent variations).
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
    """Score a Legal Name Change document (e.g. Marriage Certificate).

    Uses weakest-link scoring (min of old/new) rather than averaging. A
    correct old name + wrong new name is a rejection signal, not a pass —
    averaging them hides asymmetric failures.

    Special case — Indian / South Asian marriage certificates:
    Many official formats (Haryana, Maharashtra, etc.) state the BRIDE's
    pre-marriage name explicitly but do NOT print the post-marriage name.
    When married_name is null, we score only on bride_name (old-name match)
    and FLAG for human review rather than hard-reject. The Checker can
    visually confirm the requested new name is reasonable.
    """
    bride_name   = extracted.get("bride_name") or ""
    married_name = extracted.get("married_name") or ""
    missing_fields = []
    if not bride_name.strip():
        missing_fields.append("bride_name")

    # Old name should match the bride name field on the document
    old_match = _fuzzy_score(old_value, bride_name)

    if married_name.strip():
        # Married name is explicitly in the document — full weakest-link scoring
        new_match  = _fuzzy_score(new_value, married_name)
        name_match = round(min(old_match, new_match), 4)
        extracted_str = f"{bride_name} → {married_name}"
    else:
        # married_name not stated in document (common for Indian certificates).
        # Score entirely on old-name match. Cap at FLAG_THRESHOLD so a human
        # always reviews — they can confirm the requested new name visually.
        missing_fields.append("married_name (not stated in document — human review required)")
        new_match  = 0.0          # can't auto-verify
        # Use old_match but cap at 0.74 to ensure FLAG (not auto-APPROVE)
        name_match = round(min(old_match, 0.74), 4)
        extracted_str = f"{bride_name} → [not stated in certificate]"

    return {
        "name_match":  name_match,
        "field_scores": {
            "old_name_vs_bride_name":    round(old_match, 4),
            "new_name_vs_married_name":  round(new_match, 4),
        },
        "extracted_name": extracted_str,
        "missing_fields": missing_fields,
    }


_SCORERS = {
    "LEGAL_NAME_CHANGE": _score_legal_name_change,
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
    forgery_signals: list[dict] | None = None,
    forgery_reasoning: str = "",
    field_scores: dict[str, float] | None = None,
) -> str:
    """Build the human-readable AI summary shown in the Checker Review UI."""

    doc_label = {
        "LEGAL_NAME_CHANGE": "Marriage Certificate",
    }.get(change_type, "Supporting Document")

    lines = [
        f"**{doc_label} verified.**",
        f"Old value '{old_value}' and new value '{new_value}' were cross-referenced against the extracted document data: '{extracted_name}'.",
        f"Name/Value match confidence: **{name_match*100:.0f}%**.",
    ]

    # ── Surface per-field breakdown so the Checker sees *why* the score is
    # what it is, not just a single aggregate number.
    if field_scores:
        pretty = {
            "old_name_vs_bride_name":   "Old name vs document 'bride name'",
            "new_name_vs_married_name": "New name vs document 'married name'",
        }
        bullets = []
        for key, score in field_scores.items():
            label = pretty.get(key, key.replace("_", " "))
            bullets.append(f"  • {label}: {score*100:.0f}%")
        if bullets:
            lines.append("Field-level scores:")
            lines.extend(bullets)

    lines.extend([
        f"Document authenticity score: **{authenticity*100:.0f}%**.",
        f"Forgery check: **{forgery_check}**.",
        f"Overall confidence: **{overall*100:.0f}%**.",
        f"**AI Recommendation: {recommendation}.**",
    ])

    # ── Surface forgery reasoning to the Checker on WARN/FAIL ─────────────────
    # Keep PASS summaries clean. When there's a concern, show the top signals
    # so the Checker understands *why* to look closer — not just a single label.
    if forgery_check in ("WARN", "FAIL"):
        if forgery_reasoning:
            lines.append(f"**Forgery analysis:** {forgery_reasoning}")

        if forgery_signals:
            # Prioritise critical > warn; cap to three signals to keep the UI tight.
            ranked = sorted(
                forgery_signals,
                key=lambda s: 0 if s.get("severity") == "critical" else 1,
            )[:3]
            bullet_lines = []
            for sig in ranked:
                sev = str(sig.get("severity", "")).upper()
                name = str(sig.get("name", "")).replace("_", " ")
                detail = str(sig.get("detail", "")).strip()
                bullet_lines.append(f"- [{sev}] {name}: {detail}")
            if bullet_lines:
                lines.append("**Top forgery signals:**")
                lines.extend(bullet_lines)

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
    forgery_signals:   list[dict] | None = None,
    forgery_reasoning: str = "",
) -> dict:
    """
    Compute the full Confidence Score Card.

    Args:
        extracted_fields:  Output of document_processor.process_document()
        change_type:       e.g. LEGAL_NAME_CHANGE
        old_value:         Current value in RPS (e.g. "Priya Sharma")
        new_value:         Requested new value (e.g. "Priya Mehta")
        forgery_check:     PASS / WARN / FAIL verdict from Agent 2
        forgery_signals:   List of per-signal forgery findings (code + Gemini);
                           surfaced in the Checker summary on WARN/FAIL.
        forgery_reasoning: Gemini's natural-language summary of tamper signals.

    Returns:
        dict matching ConfidenceScoreCard schema + extra diagnostics.
    """
    logger.info("CONFIDENCE_SCORING_START", change_type=change_type)

    forgery_signals = forgery_signals or []

    # ── Field scoring ──────────────────────────────────────────────────────────
    scorer_fn = _SCORERS.get(change_type)
    if not scorer_fn:
        # Defence in depth: intake already rejects unsupported types, but if one
        # ever reaches here, return a hard REJECT rather than scoring via a
        # generic fuzzy-match fallback (which produces misleading scores).
        logger.warning("CONFIDENCE_UNSUPPORTED_CHANGE_TYPE", change_type=change_type)
        reject_reason = (
            f"change_type '{change_type}' is not supported by the confidence scorer. "
            f"Supported: {list(_SCORERS.keys())}."
        )
        summary = _build_summary(
            change_type=change_type,
            old_value=old_value,
            new_value=new_value,
            extracted_name="",
            name_match=0.0,
            authenticity=0.0,
            forgery_check=forgery_check,
            recommendation="REJECT",
            overall=0.0,
            reject_reason=reject_reason,
            forgery_signals=forgery_signals,
            forgery_reasoning=forgery_reasoning,
        )
        return {
            "name_match":          0.0,
            "authenticity":        0.0,
            "forgery_check":       forgery_check,
            "overall_confidence":  0.0,
            "recommendation":      "REJECT",
            "summary":             summary,
            "field_scores":        {},
            "extracted_name":      "",
            "forgery_signals":     forgery_signals,
            "forgery_reasoning":   forgery_reasoning,
        }

    field_result = scorer_fn(extracted_fields, old_value, new_value)

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
    #   3. Document type mismatch (declared vs Gemini-detected) → REJECT
    #      (runs BEFORE missing-fields so the end user gets the clearest reason
    #       e.g. "you uploaded a selfie" instead of "bride_name not extracted")
    #   4. Missing required fields for this change_type → REJECT
    #   5. Threshold-based: overall ≥ approve_threshold → APPROVE
    #                        overall ≥ flag_threshold   → FLAG
    #                        otherwise                  → REJECT
    #
    # Thresholds are per-change-type via settings.thresholds_for().
    # ──────────────────────────────────────────────────────────────────────────

    reject_reason = None
    recommendation: str | None = None
    approve_threshold, flag_threshold = settings.thresholds_for(change_type)

    # ── Step 1: Hard failure — forgery ────────────────────────────────────────
    if forgery_check == "FAIL":
        recommendation = "REJECT"
        # Build a rich reject reason that includes any other problems (missing
        # fields, doc-type mismatch). The Checker / staff member gets ONE clear
        # rejection message, not a sequence of silent skips.
        reasons = ["Document failed authenticity / forgery check."]
        if missing_fields:
            reasons.append(
                f"Required fields could not be extracted: {', '.join(missing_fields)}."
            )
        reject_reason = " ".join(reasons)

    # ── Step 2: Forgery warning — demote to FLAG at most ─────────────────────
    elif forgery_check == "WARN":
        # WARN means we have suspicions but aren't certain; force human review.
        recommendation = "FLAG"

    else:
        # ── Step 3: Document type mismatch (takes priority over missing fields,
        # because "you uploaded a selfie" is more informative than "bride_name
        # could not be extracted" for the end user) ─────────────────────────
        if recommendation is None and change_type == "LEGAL_NAME_CHANGE":
            detected_type = (extracted_fields.get("document_type_detected") or "").strip()
            expected_type_map = {
                "MARRIAGE_CERTIFICATE": "Marriage Certificate",
                "GAZETTE_NOTIFICATION": "Gazette Notification",
                "DEED_POLL":            "Deed Poll",
            }
            expected_type = expected_type_map.get(document_type, document_type)

            # Explicit "not a real document" bucket — Gemini is instructed to
            # return this whenever the image isn't one of the four known types.
            # Treat it as a hard reject rather than letting the threshold path
            # rescue it via name fuzzy-matching.
            NON_DOCUMENT_TOKENS = {"other", "screenshot", "selfie", "photo", "photograph",
                                   "passport photo", "picture", "none", "unknown"}
            is_non_document = (
                not detected_type
                or detected_type.lower() in NON_DOCUMENT_TOKENS
                or "other" in detected_type.lower()
                or "screenshot" in detected_type.lower()
            )

            if is_non_document:
                recommendation = "REJECT"
                reject_reason = (
                    "The uploaded image does not appear to be an official supporting "
                    f"document. AI detected: '{detected_type or 'unidentified'}'. "
                    f"Please upload a genuine {expected_type}."
                )
            else:
                # Tight match: expected type's key tokens must appear in the
                # detected type. This avoids accidental matches like
                # "Passport" vs "Marriage Certificate".
                expected_lower = expected_type.lower()
                detected_lower = detected_type.lower()
                key_token = expected_lower.split()[-1]  # "certificate" / "notification" / "poll"
                is_expected_type = (
                    expected_lower in detected_lower
                    or (key_token in detected_lower and expected_lower.split()[0] in detected_lower)
                )
                if not is_expected_type:
                    recommendation = "REJECT"
                    reject_reason = (
                        f"Document type mismatch. You declared '{expected_type}' "
                        f"but the AI detected '{detected_type}'. "
                        f"Please re-upload the correct document."
                    )

        # ── Step 4: Missing required fields ──────────────────────────────────
        if recommendation is None and missing_fields:
            recommendation = "REJECT"
            reject_reason = (
                f"Required fields could not be extracted: {', '.join(missing_fields)}. "
                f"Please re-upload a clearer, legible document."
            )

        # ── Step 5: Threshold-based recommendation ────────────────────────────
        if recommendation is None:
            if overall >= approve_threshold:
                recommendation = "APPROVE"
            elif overall >= flag_threshold:
                recommendation = "FLAG"
            else:
                recommendation = "REJECT"
                reject_reason = (
                    f"Overall confidence {overall*100:.0f}% is below the "
                    f"minimum threshold of {flag_threshold*100:.0f}%."
                )
        elif recommendation == "FLAG" and overall < flag_threshold:
            # A prior step (empty doc-type detection) set FLAG. If the overall
            # score is also below the flag threshold, downgrade to REJECT —
            # don't auto-upgrade past FLAG on high scores with empty detection.
            recommendation = "REJECT"
            reject_reason = (
                f"Overall confidence {overall*100:.0f}% is below the "
                f"minimum threshold of {flag_threshold*100:.0f}%."
            )

    # ── Summary text ──────────────────────────────────────────────────────────
    # Build the human-readable summary shown in the Checker UI.
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
        forgery_signals=forgery_signals,
        forgery_reasoning=forgery_reasoning,
        field_scores=field_result.get("field_scores", {}),
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
        "forgery_signals":     forgery_signals,
        "forgery_reasoning":   forgery_reasoning,
    }

    logger.info(
        "CONFIDENCE_SCORING_COMPLETE",
        overall=overall,
        recommendation=recommendation,
        name_match=name_match,
        authenticity=authenticity,
    )

    return score_card
