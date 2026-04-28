"""
validation_agent.py — Agent 1: RPS Cross-Reference Validation.

Responsibility:
  - Verify the customer exists in the RPS (core banking system)
  - Confirm the 'old_value' matches what is currently stored in RPS
  - Return a structured validation result before any document processing begins

Design note: In production this agent would make an authenticated REST call to
the RPS microservice. Here we query the rps_records table in PostgreSQL, which
mirrors the shape of a real RPS response and is visible/queryable in the DB.
Falls back to the in-memory config dict only when no DB session is provided
(e.g. unit tests running without a database).
"""

from __future__ import annotations
from typing import Optional

from app.config import settings
from app.services.observability import get_logger

logger = get_logger("validation_agent")


class ValidationResult:
    def __init__(
        self,
        valid: bool,
        customer_found: bool,
        rps_current_value: Optional[str],
        mismatch_fields: list,
        error: Optional[str] = None,
    ):
        self.valid = valid
        self.customer_found = customer_found
        self.rps_current_value = rps_current_value
        self.mismatch_fields = mismatch_fields
        self.error = error

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "customer_found": self.customer_found,
            "rps_current_value": self.rps_current_value,
            "mismatch_fields": self.mismatch_fields,
            "error": self.error,
        }


def run_validation(
    customer_id: str,
    change_type: str,
    old_value: str,
    db=None,
) -> ValidationResult:
    """
    Validate the intake request against the RPS record.

    Steps:
      1. Look up customer_id in rps_records (PostgreSQL) when db is provided,
         or fall back to the in-memory MOCK_RPS_RECORDS dict.
      2. Map change_type to the correct RPS field.
      3. Compare old_value (staff-submitted) against the RPS value.

    Args:
        customer_id:  Bank customer identifier (e.g. C001)
        change_type:  LEGAL_NAME_CHANGE (only supported type)
        old_value:    Current value as claimed by staff
        db:           SQLAlchemy session — queries rps_records table when provided

    Returns:
        ValidationResult with valid=True if customer exists and old_value matches.
    """
    logger.info("VALIDATION_START", customer_id=customer_id, change_type=change_type)

    # ── Step 1: Customer lookup ─────────────────────────────────────────────────
    rps_record_data: Optional[dict] = None

    if db is not None:
        # Primary path: query PostgreSQL rps_records table
        try:
            from app.database import RpsRecord
            row = db.query(RpsRecord).filter(RpsRecord.customer_id == customer_id).first()
            if row:
                rps_record_data = {
                    "name":    row.name,
                    "dob":     row.dob,
                    "address": row.address,
                    "phone":   row.phone,
                    "email":   row.email,
                }
        except Exception as exc:
            logger.warning("RPS_DB_LOOKUP_FAILED", error=str(exc), fallback="config_dict")
            # Fall through to config dict below

    if rps_record_data is None:
        # Fallback: in-memory config dict (unit tests / no DB context)
        rps_record_data = settings.MOCK_RPS_RECORDS.get(customer_id)

    if not rps_record_data:
        logger.warning("VALIDATION_CUSTOMER_NOT_FOUND", customer_id=customer_id)
        return ValidationResult(
            valid=False,
            customer_found=False,
            rps_current_value=None,
            mismatch_fields=["customer_id"],
            error=f"Customer '{customer_id}' not found in RPS.",
        )

    # ── Step 2: Map change_type → RPS field ────────────────────────────────────
    field_map = {
        "LEGAL_NAME_CHANGE": "name",
    }
    rps_field = field_map.get(change_type)
    if not rps_field:
        return ValidationResult(
            valid=False,
            customer_found=True,
            rps_current_value=None,
            mismatch_fields=["change_type"],
            error=f"Unsupported change_type: '{change_type}'.",
        )

    rps_current_value = rps_record_data.get(rps_field, "")

    # ── Step 3: Old-value cross-check ──────────────────────────────────────────
    if old_value.strip().lower() != rps_current_value.strip().lower():
        logger.warning(
            "VALIDATION_MISMATCH",
            customer_id=customer_id,
            submitted=old_value,
            rps_value=rps_current_value,
        )
        return ValidationResult(
            valid=False,
            customer_found=True,
            rps_current_value=rps_current_value,
            mismatch_fields=[rps_field],
            error=(
                f"The current value submitted for customer '{customer_id}' "
                f"does not match what is on record. "
                f"Please verify the details and re-submit."
            ),
        )

    logger.info(
        "VALIDATION_PASSED",
        customer_id=customer_id,
        rps_field=rps_field,
        rps_value=rps_current_value,
    )
    return ValidationResult(
        valid=True,
        customer_found=True,
        rps_current_value=rps_current_value,
        mismatch_fields=[],
    )
