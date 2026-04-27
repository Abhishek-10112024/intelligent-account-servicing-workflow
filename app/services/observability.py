"""
observability.py — Structured logging and audit trail for IASW.

Every agent step, human decision, and RPS write is recorded in two places:
  1. logs/iasw.log  — structured JSON log file (structlog)
  2. audit_log DB table — queryable audit trail for compliance

Design rationale: Two-layer observability ensures logs survive DB failures
and vice versa.  The structured JSON format makes logs ingestible by any
log aggregator (Splunk, Datadog, CloudWatch) without further parsing.
"""

import json
import uuid
import logging
from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from app.config import settings
from app.database import AuditLog


# ── File handler setup ────────────────────────────────────────────────────────
settings.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.LOG_FILE, encoding="utf-8"),
    ],
    format="%(message)s",
)

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger("iasw")


def log_agent_step(
    db: Session,
    request_id: str,
    actor: str,
    action: str,
    detail: dict,
) -> None:
    """
    Record one agent step to both the structured log file and the audit_log table.

    Args:
        db:         SQLAlchemy session
        request_id: UUID of the PendingRequest being processed
        actor:      Name of the agent or human actor (e.g. 'validation_agent')
        action:     Short action label (e.g. 'VALIDATION_PASSED')
        detail:     Arbitrary dict with action-specific metadata
    """
    detail_json = json.dumps(detail, default=str)

    # 1. Structured log
    logger.info(
        action,
        request_id=request_id,
        actor=actor,
        **detail,
    )

    # 2. DB audit trail
    audit_entry = AuditLog(
        id=str(uuid.uuid4()),
        request_id=request_id,
        actor=actor,
        action=action,
        detail=detail_json,
        created_at=datetime.utcnow(),
    )
    db.add(audit_entry)
    db.commit()


def get_logger(name: str = "iasw"):
    """Return a bound structlog logger for a given module name."""
    return structlog.get_logger(name)
