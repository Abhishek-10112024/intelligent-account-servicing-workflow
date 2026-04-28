"""
models.py — Pydantic request/response schemas for IASW API.

These are separate from the SQLAlchemy ORM models in database.py.
Pydantic models handle API serialisation; SQLAlchemy handles persistence.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── Intake ────────────────────────────────────────────────────────────────────

class IntakeRequest(BaseModel):
    """Submitted by bank staff via the Intake Form."""
    customer_id: str = Field(..., example="C001")
    change_type: str = Field(..., example="LEGAL_NAME_CHANGE")
    old_value:   str = Field(..., example="Priya Sharma")
    new_value:   str = Field(..., example="Priya Mehta")
    document_type: str = Field(..., example="MARRIAGE_CERTIFICATE")
    submitted_by: Optional[str] = Field(None, example="staff_01")


class IntakeResponse(BaseModel):
    request_id: str
    status: str
    message: str


# ── Confidence Score Card ─────────────────────────────────────────────────────

class ConfidenceScoreCard(BaseModel):
    """Output of the Confidence Scorer agent."""
    name_match:           float   # 0.0–1.0
    authenticity:         float   # 0.0–1.0
    forgery_check:        str     # PASS / FAIL / WARN
    overall_confidence:   float   # weighted average
    recommendation:       str     # APPROVE / FLAG / REJECT
    summary:              str     # Human-readable summary for Checker UI


# ── Pending Request (API read model) ─────────────────────────────────────────

class PendingRequestRead(BaseModel):
    """Returned by the Checker queue endpoint."""
    id:                   str
    change_type:          str
    customer_id:          str
    old_value:            Optional[str]
    new_value:            Optional[str]
    extracted_value:      Optional[str]
    document_type:        Optional[str]
    filenet_ref_id:       Optional[str]
    confidence_name:              Optional[float]
    confidence_authenticity:      Optional[float]
    forgery_check:                Optional[str]
    ai_summary:           Optional[str]
    ai_recommendation:    Optional[str]
    overall_status:       str
    checker_id:           Optional[str]
    checker_decision:     Optional[str]
    checker_notes:        Optional[str]
    created_at:           Optional[datetime]
    updated_at:           Optional[datetime]
    decided_at:           Optional[datetime]

    class Config:
        from_attributes = True


# ── Checker Decision ──────────────────────────────────────────────────────────

class CheckerDecision(BaseModel):
    """Posted by the Checker Supervisor when approving or rejecting.

    Note: `checker_id` is IGNORED by the server — the admin's identity is taken
    from the JWT. The field is kept for backward compatibility only.
    """
    request_id:      str
    checker_id:      Optional[str] = Field(None, deprecated=True, description="Ignored; taken from JWT.")
    decision:        str = Field(..., example="APPROVED")   # APPROVED | REJECTED
    notes:           Optional[str] = Field(None, example="All documents verified.")


class CheckerDecisionResponse(BaseModel):
    request_id:  str
    status:      str
    rps_updated: bool
    message:     str


# ── Audit Log (API read model) ────────────────────────────────────────────────

class AuditLogRead(BaseModel):
    id:         str
    request_id: str
    actor:      str
    action:     str
    detail:     Optional[str]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    """Credentials for username/password login."""
    username: str = Field(..., example="admin")
    password: str = Field(..., example="admin123")


class TokenResponse(BaseModel):
    """JWT response returned by /api/auth/login."""
    access_token: str
    token_type:   str = "bearer"
    expires_in:   int            # seconds until expiry
    username:     str
    role:         str            # USER | ADMIN


class MeResponse(BaseModel):
    """Returned by /api/auth/me so the client can show logged-in state."""
    username: str
    role:     str
    active:   bool


class RegisterRequest(BaseModel):
    """Public registration — always creates a PENDING user registration (role=USER)."""
    username: str = Field(..., min_length=3, max_length=32, example="alice")
    password: str = Field(..., min_length=6, max_length=72, example="s3cret!!")


class RegistrationRead(BaseModel):
    """Registration request as seen by admins."""
    id:             str
    username:       str
    requested_role: str
    status:         str
    decision_by:    Optional[str]
    decision_at:    Optional[datetime]
    decision_notes: Optional[str]
    created_at:     Optional[datetime]

    class Config:
        from_attributes = True


class RegistrationDecision(BaseModel):
    """Admin decision on a pending registration."""
    registration_id: str
    decision:        str = Field(..., example="APPROVED")   # APPROVED | REJECTED
    notes:           Optional[str] = None
