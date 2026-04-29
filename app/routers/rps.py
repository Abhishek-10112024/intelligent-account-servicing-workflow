"""
rps.py — Mock RPS (Core Banking) Write Microservice.

This module simulates the Real-time Processing System (RPS) write-call.

CRITICAL DESIGN CONSTRAINT:
  This endpoint is NEVER called directly by the frontend or AI agents.
  It is ONLY callable from checker.py after a Checker has explicitly approved.
  The function execute_rps_write() is imported and called by checker.py.
"""

import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.services.observability import get_logger
from app.services.auth import require_admin
from app.database import User, RpsRecord, get_db

router = APIRouter(prefix="/api/rps", tags=["RPS Mock"])
logger = get_logger("rps_mock")


def execute_rps_write(
    request_id:  str,
    customer_id: str,
    change_type: str,
    new_value:   str,
    checker_id:  str,
    db:          Session,
) -> dict:
    """
    Perform the persistent RPS write. Only called after Checker approval.

    Steps:
      1. Verify the customer exists in the rps_records table
      2. Map change_type to the correct RPS field
      3. Apply the update to the PostgreSQL table
      4. COMMIT the transaction to make it permanent
      5. Invalidate the Redis RPS cache for this customer
      6. Return success/failure result
    """
    # ── Guard: customer must exist in RPS ─────────────────────────────────────
    record = db.query(RpsRecord).filter(RpsRecord.customer_id == customer_id).first()
    if not record:
        logger.error("RPS_WRITE_CUSTOMER_NOT_FOUND", customer_id=customer_id)
        return {
            "success": False,
            "message": f"Customer '{customer_id}' not found in RPS records.",
            "rps_transaction_id": None,
        }

    # ── Map change_type → RPS field ───────────────────────────────────────────
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

    old_rps_value = getattr(record, rps_field, "")

    # ── Apply the update ──────────────────────────────────────────────────────
    setattr(record, rps_field, new_value)
    record.updated_at = datetime.utcnow()
    
    # CRITICAL: Commit the change to the persistent database!
    db.commit()
    db.refresh(record)
    
    rps_txn_id = f"RPS-TXN-{str(uuid.uuid4()).upper()[:10]}"

    logger.info(
        "RPS_WRITE_EXECUTED_PERSISTENT",
        rps_transaction_id=rps_txn_id,
        request_id=request_id,
        customer_id=customer_id,
        field=rps_field,
        old_value=old_rps_value,
        new_value=new_value,
        approved_by=checker_id,
    )

    # ── Invalidate Redis RPS cache ────────────────────────────────────────────
    try:
        import asyncio
        from app.services.cache import invalidate_rps_cache
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(invalidate_rps_cache(customer_id))
        else:
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


@router.get("/state", summary="Inspect current persistent RPS state (debug only)")
def get_rps_state(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin)
):
    """
    Debug endpoint to inspect the current persistent RPS state in PostgreSQL.
    Sorted by last update (newest first).
    """
    # Sorting by updated_at DESC so you see the latest change at the top
    records = db.query(RpsRecord).order_by(RpsRecord.updated_at.desc()).all()
    
    # Convert to dict for response
    state = {
        r.customer_id: {
            "name": r.name,
            "dob": r.dob,
            "address": r.address,
            "phone": r.phone,
            "email": r.email,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None
        } for r in records
    }
    
    return {
        "note": "Persistent RPS state from PostgreSQL database.",
        "records": state,
    }
