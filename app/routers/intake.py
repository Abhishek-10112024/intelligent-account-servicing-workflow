"""
intake.py — POST /api/intake (Async Processing)

Staff submission endpoint with async background processing.
Accepts multipart form, queues pipeline execution, returns 202 Accepted immediately.

Flow:
  1. Save uploaded file to temp location
  2. Enqueue pipeline task (returns task_id immediately)
  3. Return 202 ACCEPTED with task_id
  4. Pipeline runs in background
  5. Check status via GET /api/tasks/{task_id}
"""

import tempfile
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status, Request
from fastapi.responses import JSONResponse

from app.services.async_tasks import enqueue_pipeline, task_manager
from app.services.observability import get_logger
from app.services.rate_limiter import limiter
from app.services.auth import get_current_user
from app.database import get_db, PendingRequest, User
from sqlalchemy.orm import Session
from fastapi import Depends

router = APIRouter(prefix="/api", tags=["Intake"])
logger = get_logger("intake_router")


# ── Supported scope ──────────────────────────────────────────────────────────
# This prototype currently supports Legal Name Change only. Any other
# change_type is rejected at intake with a clear error.
SUPPORTED_CHANGE_TYPES = {"LEGAL_NAME_CHANGE"}
SUPPORTED_DOCUMENT_TYPES = {"MARRIAGE_CERTIFICATE", "GAZETTE_NOTIFICATION", "DEED_POLL"}


class IntakeAcceptedResponse:
    """Response model for 202 Accepted."""
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.status = "QUEUED"
        self.message = f"Request queued for processing. Task ID: {task_id}. Check status via GET /api/tasks/{task_id}"


@router.post(
    "/intake",
    status_code=202,
    summary="Submit a change request (async)"
)
@limiter.limit("10/minute")
async def submit_intake(
    request:       Request,
    customer_id:   str      = Form(..., description="Bank customer ID, e.g. C001"),
    change_type:   str      = Form(..., description="LEGAL_NAME_CHANGE (only supported change type)"),
    old_value:     str      = Form(..., description="Current value as stored in RPS"),
    new_value:     str      = Form(..., description="Requested new value"),
    document_type: str      = Form(..., description="MARRIAGE_CERTIFICATE | GAZETTE_NOTIFICATION | DEED_POLL"),
    submitted_by:  str      = Form("staff_unknown", description="Staff ID submitting the request"),
    document:      UploadFile = File(..., description="Supporting document — image (JPG/PNG) or PDF"),
    current_user:  User     = Depends(get_current_user),
):
    """
    Accept a change request and queue the IASW pipeline for async processing.

    Requires a valid JWT (any authenticated user). `submitted_by` is derived from
    the logged-in user and OVERRIDES any value supplied in the form.

    Returns:
        202 ACCEPTED with task_id for polling status.
        Use GET /api/tasks/{task_id} to check pipeline status and results.
    
    Rate Limit:
        10 requests per minute per IP address.
    """
    # Identity comes from the JWT, not the form body — prevents impersonation.
    submitted_by = current_user.username

    logger.info(
        "INTAKE_RECEIVED",
        customer_id=customer_id,
        change_type=change_type,
        submitted_by=submitted_by,
        filename=document.filename,
    )

    # ── Input Validation ───────────────────────────────────────────────────────
    if not document.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document filename is required"
        )
    
    if document.size and document.size > 20 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File size exceeds 20 MB limit"
        )

    # ── Scope guard — Legal Name Change only ──────────────────────────────────
    # Only LEGAL_NAME_CHANGE is supported in this prototype. Rejecting here keeps
    # unsupported flows out of the pipeline entirely (defence in depth alongside
    # the change-type check in the confidence scorer).
    if change_type not in SUPPORTED_CHANGE_TYPES:
        logger.warning(
            "INTAKE_UNSUPPORTED_CHANGE_TYPE",
            customer_id=customer_id,
            change_type=change_type,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"change_type '{change_type}' is not supported. "
                f"This prototype currently supports: {sorted(SUPPORTED_CHANGE_TYPES)}."
            ),
        )

    if document_type not in SUPPORTED_DOCUMENT_TYPES:
        logger.warning(
            "INTAKE_UNSUPPORTED_DOCUMENT_TYPE",
            customer_id=customer_id,
            document_type=document_type,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"document_type '{document_type}' is not supported for Legal Name Change. "
                f"Supported: {sorted(SUPPORTED_DOCUMENT_TYPES)}."
            ),
        )

    # ── Duplicate submission guard ─────────────────────────────────────────────
    # Block if this customer already has ANY open request of the same change_type.
    # Rule: one active request per customer per change type — regardless of what
    # new value is being requested. A customer cannot bypass this by requesting
    # a different new name.
    from app.database import SessionLocal
    _dup_db = SessionLocal()
    try:
        _existing = (
            _dup_db.query(PendingRequest)
            .filter(
                PendingRequest.customer_id    == customer_id,
                PendingRequest.change_type    == change_type,
                PendingRequest.overall_status == "AI_VERIFIED_PENDING_HUMAN",
            )
            .first()
        )
        if _existing:
            logger.warning(
                "INTAKE_DUPLICATE_BLOCKED",
                customer_id=customer_id,
                change_type=change_type,
                existing_request_id=_existing.id,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Customer '{customer_id}' already has an open {change_type} request "
                    f"(request_id: {_existing.id}) awaiting Checker review. "
                    f"No new requests can be submitted until that request is approved or rejected."
                ),
            )
    finally:
        _dup_db.close()

    # ── Save upload to temp file ───────────────────────────────────────────────
    # Server-side rename: drop the user-supplied filename entirely and save as
    # <tempdir>/<random>.<validated-ext> — closes the double-extension surface
    # and ensures downstream code never trusts a user-controlled name.
    # The extension is picked from an allow-list; if the supplied extension is
    # not in it, we reject the upload here rather than later.
    _ALLOWED_SAVE_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
    tmp_path = None
    try:
        raw_suffix = Path(document.filename).suffix.lower() if document.filename else ""
        if raw_suffix not in _ALLOWED_SAVE_EXTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"File extension '{raw_suffix or '(none)'}' is not allowed. "
                    f"Accepted: {sorted(_ALLOWED_SAVE_EXTS)}."
                ),
            )

        # NamedTemporaryFile gives us a server-chosen basename; we only append
        # the validated extension. User-supplied filename is discarded.
        with tempfile.NamedTemporaryFile(delete=False, suffix=raw_suffix) as tmp:
            content = await document.read()

            if not content:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Uploaded file is empty"
                )

            tmp.write(content)
            tmp_path = Path(tmp.name)

        # ── Enqueue pipeline task (non-blocking) ───────────────────────────────
        task_id = await enqueue_pipeline(
            payload={
                "customer_id": customer_id,
                "change_type": change_type,
                "old_value": old_value,
                "new_value": new_value,
                "document_type": document_type,
                "file_path": str(tmp_path),
            }
        )

        logger.info(
            "INTAKE_QUEUED",
            task_id=task_id,
            customer_id=customer_id,
        )

        return {
            "task_id": task_id,
            "status": "QUEUED",
            "message": f"Request queued for processing. Check status via GET /api/tasks/{task_id}",
            "poll_url": f"/api/tasks/{task_id}",
        }

    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    
    except Exception as exc:
        logger.error("INTAKE_QUEUE_ERROR", error=str(exc), exc_info=True)
        
        # Clean up temp file on error
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup temp file: {cleanup_error}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue request: {str(exc)}"
        )
