"""
auth.py — Authentication and registration endpoints.

Public:
  POST /api/auth/login       — Exchange username/password for a JWT
  POST /api/auth/register    — Submit a registration request (status=PENDING)
  GET  /api/auth/me          — Return the currently logged-in user

Admin-only:
  GET  /api/auth/registrations                 — List pending registrations
  POST /api/auth/registrations/decide          — Approve or reject a registration

Notes:
  - /api/auth/login accepts EITHER JSON {username, password} OR OAuth2 form data
    (so Swagger's Authorize modal works out of the box).
  - Registrations never create an active user directly. An ADMIN must approve.
  - `checker_id` is derived from the JWT for all Checker actions (see checker.py).
"""

import uuid
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db, User, UserRegistration
from app.models import (
    LoginRequest,
    TokenResponse,
    MeResponse,
    RegisterRequest,
    RegistrationRead,
    RegistrationDecision,
)
from app.services.auth import (
    authenticate_user,
    create_access_token,
    get_current_user,
    require_admin,
    hash_password,
)
from app.services.observability import get_logger

router = APIRouter(prefix="/api/auth", tags=["Auth"])
logger = get_logger("auth_router")


# ── Login ─────────────────────────────────────────────────────────────────────

async def _extract_credentials(request: Request) -> tuple[str, str]:
    """
    Accept credentials in either shape:
      1. JSON body  : {"username": "...", "password": "..."}
      2. Form data  : OAuth2PasswordRequestForm (username, password)
    This lets both the React app (JSON) and Swagger's Authorize (form) work.
    """
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        body = await request.json()
        return body.get("username", ""), body.get("password", "")
    # Fall back to form-encoded
    form = await request.form()
    return form.get("username", ""), form.get("password", "")


@router.post("/login", response_model=TokenResponse, summary="Login and get a JWT")
async def login(request: Request, db: Session = Depends(get_db)):
    """
    Exchange username + password for a signed JWT.

    Failure modes (all 401 with identical messaging to avoid user-enumeration):
      - Unknown user
      - Wrong password
      - Deactivated account
    """
    username, password = await _extract_credentials(request)
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="username and password are required.",
        )

    user = authenticate_user(db, username, password)
    if not user:
        logger.info("LOGIN_FAILED", username=username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = create_access_token(username=user.username, role=user.role)
    logger.info("LOGIN_OK", username=user.username, role=user.role)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        username=user.username,
        role=user.role,
    )


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/me", response_model=MeResponse, summary="Current user")
def me(user: User = Depends(get_current_user)):
    return MeResponse(username=user.username, role=user.role, active=str(user.active).lower() == "true")


# ── Register (admin-approved) ────────────────────────────────────────────────

@router.post(
    "/register",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a registration request (admin approval required)",
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """
    Register a new USER account. The account is NOT created immediately —
    an admin must approve the request. The requester is told only that the
    request was accepted, regardless of whether the username collides (to avoid
    enumeration of existing usernames).

    Admin role cannot be self-requested.
    """
    uname = payload.username.strip()
    if not uname:
        raise HTTPException(status_code=400, detail="username is required.")

    # Silently ignore duplicates to avoid leaking which usernames exist.
    # We still record an internal log for auditability.
    existing_user = db.query(User).filter(User.username == uname).first()
    existing_reg = (
        db.query(UserRegistration)
        .filter(UserRegistration.username == uname, UserRegistration.status == "PENDING")
        .first()
    )
    if existing_user or existing_reg:
        logger.info("REGISTER_DUPLICATE_SILENT", username=uname)
        return {
            "status": "PENDING",
            "message": "Registration received. An administrator will review it.",
        }

    reg = UserRegistration(
        id=str(uuid.uuid4()),
        username=uname,
        password_hash=hash_password(payload.password),
        requested_role="USER",   # admin role can never be self-requested
        status="PENDING",
        created_at=datetime.utcnow(),
    )
    db.add(reg)
    db.commit()
    logger.info("REGISTER_SUBMITTED", username=uname, registration_id=reg.id)
    return {
        "status": "PENDING",
        "message": "Registration received. An administrator will review it.",
    }


# ── Admin: list pending registrations ────────────────────────────────────────

@router.get(
    "/registrations",
    response_model=list[RegistrationRead],
    summary="List pending registration requests (admin only)",
)
def list_registrations(
    status_filter: str = "PENDING",
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    q = db.query(UserRegistration)
    if status_filter and status_filter != "ALL":
        q = q.filter(UserRegistration.status == status_filter)
    return q.order_by(UserRegistration.created_at.desc()).all()


# ── Admin: approve or reject a registration ──────────────────────────────────

@router.post(
    "/registrations/decide",
    response_model=RegistrationRead,
    summary="Approve or reject a pending registration (admin only)",
)
def decide_registration(
    decision: RegistrationDecision,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if decision.decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(status_code=422, detail="decision must be 'APPROVED' or 'REJECTED'.")

    reg = db.query(UserRegistration).filter(UserRegistration.id == decision.registration_id).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found.")

    if reg.status != "PENDING":
        raise HTTPException(
            status_code=409,
            detail=f"Registration is in status '{reg.status}' and cannot be re-decided.",
        )

    now = datetime.utcnow()
    reg.status         = decision.decision
    reg.decision_by    = admin.username
    reg.decision_at    = now
    reg.decision_notes = decision.notes

    if decision.decision == "APPROVED":
        # Ensure the username still isn't taken (TOCTOU between submit and approval)
        clash = db.query(User).filter(User.username == reg.username).first()
        if clash:
            reg.status = "REJECTED"
            reg.decision_notes = (decision.notes or "") + " [auto: username already taken]"
            db.commit()
            logger.warning(
                "REGISTER_APPROVAL_USERNAME_TAKEN",
                username=reg.username,
                registration_id=reg.id,
            )
            raise HTTPException(
                status_code=409,
                detail=f"Username '{reg.username}' is already taken; registration auto-rejected.",
            )

        new_user = User(
            id=str(uuid.uuid4()),
            username=reg.username,
            password_hash=reg.password_hash,   # re-use the hash submitted at registration
            role=reg.requested_role or "USER",
            active="true",
            approved_by=admin.username,
            created_at=now,
        )
        db.add(new_user)
        logger.info("REGISTER_APPROVED", username=reg.username, by=admin.username)
    else:
        logger.info("REGISTER_REJECTED", username=reg.username, by=admin.username)

    db.commit()
    db.refresh(reg)
    return reg
