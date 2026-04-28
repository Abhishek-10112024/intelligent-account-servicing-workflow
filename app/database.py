"""
database.py — SQLAlchemy setup and Pending Table schema.

Two tables:
  1. pending_requests  — the core IASW staging table (one row per change request)
  2. audit_log         — immutable append-only log of every agent action and human decision

Design note: We use SQLite for zero-setup prototyping.  The schema is intentionally
compatible with PostgreSQL — swap DATABASE_URL and the engine dialect to migrate.
"""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Float, Text,
    DateTime, CheckConstraint, event
)
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# ── Engine & Session ──────────────────────────────────────────────────────────
engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()





# ── Table 1: Pending Requests ─────────────────────────────────────────────────
class PendingRequest(Base):
    """
    The central staging table for all AI-verified change requests.

    Lifecycle of 'overall_status':
      AI_VERIFIED_PENDING_HUMAN  →  APPROVED  (Checker approves)
                                 →  REJECTED  (Checker rejects)

    HITL enforcement: The /rps/write endpoint refuses to write unless
    checker_decision = 'APPROVED' and checker_id is non-null.
    """
    __tablename__ = "pending_requests"

    # ── Identity ──────────────────────────────────────────────────────────────
    id              = Column(String,  primary_key=True)           # UUID v4
    change_type     = Column(String,  nullable=False)             # e.g. LEGAL_NAME_CHANGE
    customer_id     = Column(String,  nullable=False)

    # ── Requested change ──────────────────────────────────────────────────────
    old_value       = Column(String)                              # Value currently in RPS
    new_value       = Column(String)                              # Value staff wants to set

    # ── Document extraction results ───────────────────────────────────────────
    extracted_value = Column(String)                              # What AI read from the doc
    document_type   = Column(String)                              # e.g. MARRIAGE_CERTIFICATE
    filenet_ref_id  = Column(String)                              # Mock FileNet archive ID

    # ── Confidence scores (0.0 – 1.0) ────────────────────────────────────────
    confidence_name         = Column(Float)   # Name field match score
    confidence_authenticity = Column(Float)   # Document authenticity score
    forgery_check           = Column(String)  # PASS / FAIL / WARN

    # ── AI output ─────────────────────────────────────────────────────────────
    ai_summary          = Column(Text)        # Human-readable AI summary for Checker
    ai_recommendation   = Column(String)      # APPROVE / FLAG / REJECT

    # ── Status ────────────────────────────────────────────────────────────────
    overall_status = Column(
        String,
        nullable=False,
        default="AI_VERIFIED_PENDING_HUMAN",
    )

    # ── Human Checker decision ────────────────────────────────────────────────
    checker_id       = Column(String)         # Staff ID of the Checker who acted
    checker_decision = Column(String)         # APPROVED / REJECTED
    checker_notes    = Column(Text)           # Optional notes from Checker

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    decided_at   = Column(DateTime)           # When Checker acted

    # ── DB-level HITL guard ───────────────────────────────────────────────────
    # Ensures status can only be APPROVED/REJECTED when checker_id is present
    __table_args__ = (
        CheckConstraint(
            "(overall_status IN ('AI_VERIFIED_PENDING_HUMAN', 'VALIDATION_FAILED')) OR "
            "(checker_id IS NOT NULL AND checker_decision IS NOT NULL)",
            name="chk_hitl_required"
        ),
    )


# ── Table 2: Audit Log ────────────────────────────────────────────────────────
class AuditLog(Base):
    """
    Immutable append-only table recording every agent step and human decision.
    Provides full observability without mutating the PendingRequest row.
    """
    __tablename__ = "audit_log"

    id          = Column(String,   primary_key=True)   # UUID
    request_id  = Column(String,   nullable=False)      # FK → pending_requests.id
    actor       = Column(String,   nullable=False)      # e.g. 'validation_agent', 'checker'
    action      = Column(String,   nullable=False)      # e.g. 'VALIDATION_PASSED'
    detail      = Column(Text)                          # JSON-serialised payload
    created_at  = Column(DateTime, default=datetime.utcnow)


# ── Table 3: Users ────────────────────────────────────────────────────────────
class User(Base):
    """
    Authenticated user of the system.

    Roles:
      - USER   : Can submit Intake requests; cannot access Checker/RPS/audit.
      - ADMIN  : Can access Checker queue, approve/reject intake requests,
                 approve pending user registrations, view audit trail.

    Notes:
      - password_hash stores a bcrypt hash; plaintext passwords never land here.
      - `active` gates login. Admin-approved registrations flip this to True.
    """
    __tablename__ = "users"

    id            = Column(String, primary_key=True)   # UUID
    username      = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    role          = Column(String, nullable=False, default="USER")   # USER | ADMIN
    active        = Column(String, nullable=False, default="true")   # "true" | "false"
    created_at    = Column(DateTime, default=datetime.utcnow)
    # Set when this user was created from an approved registration
    approved_by   = Column(String)                                   # admin username


# ── Table 4: User Registrations ───────────────────────────────────────────────
class UserRegistration(Base):
    """
    Self-serve registration request. Admins approve or reject these from the
    Checker UI. On approval, a row is created in `users`; on rejection, the
    registration is marked REJECTED and no user is created.

    Lifecycle of `status`:
      PENDING    → APPROVED  (admin approves, user row created)
                 → REJECTED  (admin rejects, no user row created)
    """
    __tablename__ = "user_registrations"

    id            = Column(String, primary_key=True)   # UUID
    username      = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)     # hashed at submission time
    requested_role = Column(String, nullable=False, default="USER")  # always USER (guarded server-side)
    status        = Column(String, nullable=False, default="PENDING") # PENDING | APPROVED | REJECTED
    decision_by   = Column(String)                                    # admin username
    decision_at   = Column(DateTime)
    decision_notes = Column(Text)
    created_at    = Column(DateTime, default=datetime.utcnow)


# ── DB Initialisation ─────────────────────────────────────────────────────────
def init_db():
    """Create all tables. Called once at application startup."""
    Base.metadata.create_all(bind=engine)


# ── FastAPI dependency ────────────────────────────────────────────────────────
def get_db():
    """Yield a DB session; ensure it is closed after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
