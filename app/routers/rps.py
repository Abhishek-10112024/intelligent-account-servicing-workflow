"""
rps.py — Mock RPS (Core Banking) Write Microservice.

This module simulates the Real-time Processing System (RPS) write-call.

CRITICAL DESIGN CONSTRAINT:
  This endpoint is NEVER called directly by the frontend or AI agents.
  It is ONLY callable from checker.py after a Checker has explicitly approved.
  The function execute_rps_write() is imported and called by checker.py.

In production:
  - This would be an authenticated internal microservice call
  - The RPS would apply its own idempotency, retry logic, and audit trail
  - Change would flow through an event bus (e.g., Kafka) to downstream systems

Enhanced with:
  - Redis cache invalidation for the affected customer after each write
  - Cached RPS state reads via get_cached_rps_record / set_cached_rps_record
"""

import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import APIRouter

from app.config import settings
from app.services.observability import get_logger
from app.services.auth import require_admin
from app.database import User
from fastapi import Depends

router = APIRouter(prefix="/api/rps", tags=["RPS Mock"])
logger = get_logger("rps_mock")

# In-memory mock of the RPS state (starts from MOCK_RPS_RECORDS seed)
# In production: this would be the actual core banking database
_mock_rps_state: dict = {k: dict(v) for k, v in settings.MOCK_RPS_RECORDS.items()}


def execute_rps_write(
    request_id:  str,
    customer_id: str,
    change_type: str,
    new_value:   str,
    checker_id:  str,
    db:          Session,
) -> dict:
    """
    Perform the mock RPS write. Only called after Checker approval.

    Steps:
      1. Verify the customer exists in the mock RPS state
      2. Map change_type to the correct RPS field
      3. Apply the update to the in-memory mock RPS
      4. Invalidate the Redis RPS cache for this customer (async fire-and-forget)
      5. Return success/failure result

    Args:
        request_id:  UUID of the approved PendingRequest (for audit traceability)
        customer_id: Bank customer identifier
        change_type: e.g. LEGAL_NAME_CHANGE
        new_value:   The new value to write
        checker_id:  ID of the approving Checker (for audit trail)
        db:          SQLAlchemy session (for logging only)

    Returns:
        {"success": bool, "message": str, "rps_transaction_id": str}
    """
    # ── Guard: customer must exist in RPS ─────────────────────────────────────
    if customer_id not in _mock_rps_state:
        logger.error("RPS_WRITE_CUSTOMER_NOT_FOUND", customer_id=customer_id)
        return {
            "success": False,
            "message": f"Customer '{customer_id}' not found in RPS.",
            "rps_transaction_id": None,
        }

    # ── Map change_type → RPS field ───────────────────────────────────────────
    # Only LEGAL_NAME_CHANGE is supported in this prototype.
    field_map = {
        "LEGAL_NAME_CHANGE": "name",
    }
    rps_field = field_map.get(change_type)
    if not rps_field:
        return {
            "success": False,
            "message": f"Unsupported change_type: '{change_type}'.",
            "rps_transaction_id": None,
        }

    old_rps_value = _mock_rps_state[customer_id].get(rps_field, "")

    # ── Apply the update ──────────────────────────────────────────────────────
    _mock_rps_state[customer_id][rps_field] = new_value
    rps_txn_id = f"RPS-TXN-{str(uuid.uuid4()).upper()[:10]}"

    logger.info(
        "RPS_WRITE_EXECUTED",
        rps_transaction_id=rps_txn_id,
        request_id=request_id,
        customer_id=customer_id,
        field=rps_field,
        old_value=old_rps_value,
        new_value=new_value,
        approved_by=checker_id,
        executed_at=datetime.utcnow().isoformat(),
    )

    # ── Invalidate Redis RPS cache for this customer (best-effort) ────────────
    # Fire-and-forget: we don't block the RPS write on cache availability
    try:
        import asyncio
        from app.services.cache import invalidate_rps_cache

        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Running inside an async context (FastAPI request) — schedule it
            asyncio.ensure_future(invalidate_rps_cache(customer_id))
        else:
            # Running in a sync context (tests, CLI) — run directly
            loop.run_until_complete(invalidate_rps_cache(customer_id))
    except Exception as cache_err:
        logger.warning(f"RPS_CACHE_INVALIDATE_FAILED: {cache_err}")

    return {
        "success": True,
        "message": (
            f"RPS updated: customer '{customer_id}' field '{rps_field}' "
            f"changed from '{old_rps_value}' to '{new_value}'. "
            f"Transaction ID: {rps_txn_id}"
        ),
        "rps_transaction_id": rps_txn_id,
    }


@router.get("/state", summary="Inspect current mock RPS state (debug only)")
def get_rps_state(_admin: User = Depends(require_admin)):
    """
    Debug endpoint to inspect the current in-memory mock RPS state.
    Shows the effect of approved changes.
    REMOVE or restrict in production.
    """
    return {
        "note": "Mock RPS in-memory state. Changes persist only for the current server session.",
        "records": _mock_rps_state,
    }
