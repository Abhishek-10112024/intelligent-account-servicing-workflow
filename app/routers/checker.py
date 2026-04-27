"""
checker.py — Checker Review endpoints.

GET  /api/checker/queue          — Returns all pending requests awaiting Checker review
GET  /api/checker/queue/{id}     — Returns a single request detail
POST /api/checker/decide         — Checker approves or rejects a request

HITL enforcement logic lives here:
  - Only requests with status AI_VERIFIED_PENDING_HUMAN can be acted on
  - checker_id is mandatory
  - On APPROVED, the /rps/write microservice is called internally
  - All decisions are written to the audit log

Enhanced with:
  - Redis cache on GET /checker/queue (30s TTL)
  - Cache invalidation on POST /checker/decide
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db, PendingRequest
from app.models import CheckerDecision, CheckerDecisionResponse, PendingRequestRead, AuditLogRead
from app.database import AuditLog
from app.services.observability import log_agent_step, get_logger
from app.routers.rps import execute_rps_write
from fastapi.responses import FileResponse
from app.config import settings
from app.services.cache import (
    get_cached_checker_queue,
    set_cached_checker_queue,
    invalidate_checker_cache,
)

router = APIRouter(prefix="/api/checker", tags=["Checker"])
logger = get_logger("checker_router")


@router.get("/queue", response_model=list[PendingRequestRead], summary="Get all pending requests")
async def get_checker_queue(
    status: str = "AI_VERIFIED_PENDING_HUMAN",
    db: Session = Depends(get_db),
):
    """
    Return all requests with the given status (default: awaiting human review).
    Checker UI polls this endpoint to populate the review queue.
    Redis cache: 30-second TTL to reduce DB load during rapid polling.
    """
    # Only cache the default pending queue (not custom status filters)
    use_cache = (status == "AI_VERIFIED_PENDING_HUMAN")

    if use_cache:
        cached = await get_cached_checker_queue()
        if cached is not None:
            logger.debug("CHECKER_QUEUE_CACHE_HIT")
            return cached

    records = (
        db.query(PendingRequest)
        .filter(PendingRequest.overall_status == status)
        .order_by(PendingRequest.created_at.desc())
        .all()
    )

    # Serialize to dict for caching (Pydantic can re-validate on return)
    if use_cache and records:
        serialized = [
            {c.name: getattr(r, c.name) for c in r.__table__.columns}
            for r in records
        ]
        # Convert non-serializable types (datetime, UUID) to str
        for item in serialized:
            for k, v in item.items():
                if hasattr(v, 'isoformat'):
                    item[k] = v.isoformat()
                elif hasattr(v, 'hex'):
                    item[k] = str(v)
        await set_cached_checker_queue(serialized, ttl=30)

    return records


@router.get("/queue/{request_id}", response_model=PendingRequestRead, summary="Get a single request")
def get_request_detail(request_id: str, db: Session = Depends(get_db)):
    """Return full details for a single pending request (for Checker review modal)."""
    record = db.query(PendingRequest).filter(PendingRequest.id == request_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Request '{request_id}' not found.")
    return record


@router.get("/document/{request_id}", summary="Get uploaded document image")
def get_document(request_id: str, db: Session = Depends(get_db)):
    """Return the uploaded document for the human checker to view."""
    record = db.query(PendingRequest).filter(PendingRequest.id == request_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Request not found.")
    
    dest_dir = settings.FILENET_UPLOAD_DIR / record.customer_id / request_id
    if not dest_dir.exists():
        raise HTTPException(status_code=404, detail="Document directory not found.")
    
    files = list(dest_dir.iterdir())
    if not files:
        raise HTTPException(status_code=404, detail="Document file not found.")
    
    return FileResponse(path=files[0])


@router.get("/history", response_model=list[PendingRequestRead], summary="Get decided requests")
def get_checker_history(db: Session = Depends(get_db)):
    """Return all requests that have been decided (APPROVED or REJECTED)."""
    records = (
        db.query(PendingRequest)
        .filter(PendingRequest.overall_status.in_(["APPROVED", "REJECTED"]))
        .order_by(PendingRequest.decided_at.desc())
        .limit(50)
        .all()
    )
    return records


@router.get("/audit/{request_id}", response_model=list[AuditLogRead], summary="Audit trail for a request")
def get_audit_trail(request_id: str, db: Session = Depends(get_db)):
    """Return the full audit trail for a request (all agent steps + human decisions)."""
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.request_id == request_id)
        .order_by(AuditLog.created_at.asc())
        .all()
    )
    return logs


@router.post("/decide", response_model=CheckerDecisionResponse, summary="Checker approves or rejects")
async def checker_decide(
    decision: CheckerDecision,
    db: Session = Depends(get_db),
):
    """
    ══════════════════════════════════════════════════════════
    HITL BOUNDARY — This is the ONLY path to RPS write.
    ══════════════════════════════════════════════════════════

    The Checker must:
      1. Provide their checker_id (non-empty)
      2. Submit decision = APPROVED or REJECTED

    On APPROVED:
      - Record is updated in pending_requests
      - execute_rps_write() is called (mock RPS write)
      - Audit log records the decision

    On REJECTED:
      - Status set to REJECTED
      - RPS is NOT touched
      - Audit log records the rejection

    Cache: invalidates checker queue cache after any decision.
    """
    # ── Input validation ──────────────────────────────────────────────────────
    if decision.decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(
            status_code=422,
            detail="decision must be 'APPROVED' or 'REJECTED'.",
        )
    if not decision.checker_id.strip():
        raise HTTPException(
            status_code=422,
            detail="checker_id is required. AI cannot act as a Checker.",
        )

    # ── Fetch request ─────────────────────────────────────────────────────────
    record = db.query(PendingRequest).filter(PendingRequest.id == decision.request_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Request '{decision.request_id}' not found.")

    # ── Guard: only AI_VERIFIED_PENDING_HUMAN can be acted on ─────────────────
    if record.overall_status != "AI_VERIFIED_PENDING_HUMAN":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Request is in status '{record.overall_status}' and cannot be re-decided. "
                f"Only AI_VERIFIED_PENDING_HUMAN requests can be acted on."
            ),
        )

    now = datetime.utcnow()
    rps_updated = False

    # ── Update record ─────────────────────────────────────────────────────────
    record.checker_id       = decision.checker_id
    record.checker_decision = decision.decision
    record.checker_notes    = decision.notes
    record.overall_status   = decision.decision   # APPROVED or REJECTED
    record.decided_at       = now
    record.updated_at       = now

    if decision.decision == "APPROVED":
        # ── Trigger mock RPS write ─────────────────────────────────────────────
        rps_result = execute_rps_write(
            request_id=record.id,
            customer_id=record.customer_id,
            change_type=record.change_type,
            new_value=record.new_value,
            checker_id=decision.checker_id,
            db=db,
        )
        rps_updated = rps_result["success"]
        logger.info(
            "RPS_WRITE_TRIGGERED",
            request_id=record.id,
            customer_id=record.customer_id,
            rps_success=rps_updated,
        )

    db.commit()

    # ── Invalidate checker cache so next queue poll reflects the decision ─────
    await invalidate_checker_cache()
    logger.debug("CHECKER_CACHE_INVALIDATED", request_id=record.id)

    # ── Audit log ─────────────────────────────────────────────────────────────
    log_agent_step(
        db=db,
        request_id=record.id,
        actor=f"checker:{decision.checker_id}",
        action=f"CHECKER_{decision.decision}",
        detail={
            "decision":    decision.decision,
            "checker_id":  decision.checker_id,
            "notes":       decision.notes,
            "rps_updated": rps_updated,
            "decided_at":  now.isoformat(),
        },
    )

    return CheckerDecisionResponse(
        request_id=record.id,
        status=decision.decision,
        rps_updated=rps_updated,
        message=(
            f"Request {record.id} has been {decision.decision} by {decision.checker_id}. "
            + ("RPS record updated successfully." if rps_updated else "RPS not updated.")
        ),
    )
