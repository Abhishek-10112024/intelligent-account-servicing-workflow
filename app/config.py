"""
config.py — Centralised configuration for IASW.

All environment variables are loaded here via python-dotenv.
Downstream modules import from this module rather than reading
os.environ directly, ensuring a single source of truth.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (two levels up from this file)
_BASE_DIR = Path(__file__).resolve().parent.parent
# During pytest runs, avoid implicitly loading a developer's local .env.
# Tests should be deterministic and default to mock mode unless the test runner
# explicitly sets GEMINI_API_KEY in the process environment.
_RUNNING_PYTEST = bool(os.getenv("PYTEST_CURRENT_TEST")) or ("pytest" in sys.modules)
if not _RUNNING_PYTEST:
    load_dotenv(_BASE_DIR / ".env")


class Settings:
    # ── LLM ───────────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    # Model name is configurable because available Gemini models vary by account/region.
    # Use a current Flash model by default.
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    # If enabled, Gemini failures should fail the request instead of falling back.
    GEMINI_STRICT: bool = os.getenv("GEMINI_STRICT", "false").strip().lower() in {"1", "true", "yes", "y", "on"}
    # Force mock mode (useful for tests / fully offline demos).
    # Defaults to True under pytest for deterministic test runs.
    FORCE_MOCK_LLM: bool = (
        _RUNNING_PYTEST
        or os.getenv("FORCE_MOCK_LLM", "false").strip().lower() in {"1", "true", "yes", "y", "on"}
    )
    # If no key is provided (or mock is forced), the Document Processor switches to mock mode.
    USE_MOCK_LLM: bool = FORCE_MOCK_LLM or not bool(os.getenv("GEMINI_API_KEY", "").strip())

    # ── Database ───────────────────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{_BASE_DIR}/iasw.db")

    # ── Redis Cache ────────────────────────────────────────────────────────────
    # Redis is optional for caching. If unavailable, system degrades gracefully.
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_ENABLED: bool = os.getenv("REDIS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "y", "on"}

    # ── Auth (JWT) ─────────────────────────────────────────────────────────────
    # HS256 JWT with 60-minute expiry. Secret MUST be overridden via env in prod;
    # the default is a clearly-unsafe placeholder so misconfiguration is obvious.
    JWT_SECRET: str = os.getenv("JWT_SECRET", "CHANGE-ME-IN-PRODUCTION-dev-only-secret")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
    # Seeded demo credentials — printed to stdout on first boot.
    SEED_ADMIN_USERNAME: str = os.getenv("SEED_ADMIN_USERNAME", "admin")
    SEED_ADMIN_PASSWORD: str = os.getenv("SEED_ADMIN_PASSWORD", "admin123")
    SEED_USER_USERNAME: str = os.getenv("SEED_USER_USERNAME", "user")
    SEED_USER_PASSWORD: str = os.getenv("SEED_USER_PASSWORD", "user123")

    # ── FileNet mock storage ───────────────────────────────────────────────────
    FILENET_UPLOAD_DIR: Path = _BASE_DIR / os.getenv("FILENET_UPLOAD_DIR", "uploads")

    # ── Logging ────────────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: Path = _BASE_DIR / "logs" / "iasw.log"
    # Dedicated Redis activity log — useful for demos / showing exactly
    # when & where the cache is being hit, set, or invalidated.
    REDIS_LOG_FILE: Path = _BASE_DIR / "logs" / "redis.log"

    # ── Confidence thresholds ─────────────────────────────────────────────────
    # Global defaults used when a change_type is not in CHANGE_TYPE_THRESHOLDS.
    # Requests with overall confidence at or above APPROVE_THRESHOLD get "APPROVE".
    # Requests below FLAG_THRESHOLD are rejected; between the two are flagged.
    APPROVE_THRESHOLD: float = 0.80
    FLAG_THRESHOLD: float = 0.60

    # Per-change-type thresholds. Legal name changes warrant a higher bar than
    # generic text matches because the name_match score now uses the weakest
    # field (min) rather than an average — a 0.85 auto-approve bar means BOTH
    # old and new name must clear 0.85, not just average to it.
    CHANGE_TYPE_THRESHOLDS: dict = {
        "LEGAL_NAME_CHANGE": {"approve": 0.85, "flag": 0.65},
    }

    @classmethod
    def thresholds_for(cls, change_type: str) -> tuple[float, float]:
        """Return (approve, flag) thresholds for a change_type."""
        override = cls.CHANGE_TYPE_THRESHOLDS.get(change_type)
        if override:
            return override.get("approve", cls.APPROVE_THRESHOLD), override.get("flag", cls.FLAG_THRESHOLD)
        return cls.APPROVE_THRESHOLD, cls.FLAG_THRESHOLD

    # ── App metadata ───────────────────────────────────────────────────────────
    APP_TITLE: str = "Intelligent Account Servicing Workflow"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = os.getenv("APP_ENV", "development")

    # ── Mock RPS seed data ─────────────────────────────────────────────────────
    # In production this would be fetched from the actual RPS core banking system.
    MOCK_RPS_RECORDS: dict = {
        "C001": {"name": "Priya Sharma", "dob": "1990-03-15", "address": "12 MG Road, Mumbai", "phone": "9876543210", "email": "priya.sharma@email.com"},
        "C002": {"name": "Rahul Verma",  "dob": "1985-07-22", "address": "45 Brigade Rd, Bengaluru", "phone": "9123456789", "email": "rahul.verma@email.com"},
        "C003": {"name": "Anita Nair",   "dob": "1992-11-08", "address": "7 Anna Salai, Chennai",  "phone": "9988776655", "email": "anita.nair@email.com"},
    }


settings = Settings()
