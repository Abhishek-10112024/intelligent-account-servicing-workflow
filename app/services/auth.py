"""
auth.py — Authentication and authorization primitives.

Responsibilities:
  1. Password hashing via bcrypt (passlib)
  2. JWT creation and verification (python-jose, HS256)
  3. FastAPI dependencies:
       - get_current_user    → any authenticated user
       - require_admin       → must have role == 'ADMIN'
  4. Seeding the default admin + demo user on first boot

Security notes:
  - Passwords are never logged or returned over the wire.
  - JWT payload is minimal: sub (username), role, exp, iat.
  - Inactive users cannot authenticate even with a valid token.
"""

import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.database import User, get_db, SessionLocal

logger = logging.getLogger(__name__)

# ── Password hashing ──────────────────────────────────────────────────────────
# bcrypt with default rounds (12). passlib handles salt generation.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Return a bcrypt hash for the given plaintext password."""
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash."""
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:
        # Malformed hash, etc. — treat as auth failure, not a 500.
        return False


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_access_token(*, username: str, role: str) -> tuple[str, int]:
    """
    Build a signed JWT for the given user.

    Returns:
        (token, expires_in_seconds)
    """
    expire_seconds = settings.JWT_EXPIRE_MINUTES * 60
    now = datetime.utcnow()
    payload = {
        "sub": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expire_seconds)).timestamp()),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, expire_seconds


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT. Raises HTTPException(401) on any failure.
    """
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as e:
        logger.info("JWT_DECODE_FAILED: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── FastAPI dependencies ──────────────────────────────────────────────────────
# tokenUrl points at /api/auth/login so the Swagger "Authorize" button works.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=True)


def _is_active(user: User) -> bool:
    return str(user.active).lower() == "true"


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Resolve the current user from the Authorization header.
    Rejects with 401 if:
      - token is missing / malformed / expired
      - user no longer exists
      - user has been deactivated
    """
    payload = decode_token(token)
    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not _is_active(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated.",
        )
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Require the caller to have role == 'ADMIN'."""
    if user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required.",
        )
    return user


# ── Authentication helper ────────────────────────────────────────────────────

def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    """
    Verify credentials against the users table.

    Returns:
        User on success, None on failure. Failure reasons (no user / bad password
        / inactive) are deliberately indistinguishable to the caller to avoid
        user-enumeration.
    """
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    if not _is_active(user):
        return None
    return user


# ── Seed admin + demo user on first boot ──────────────────────────────────────

def seed_default_users() -> dict:
    """
    Create the seeded admin and demo user if they don't already exist.

    Returns a dict describing which accounts were created vs. already present.
    Called once from main.py's startup hook.
    """
    result = {"created": [], "existing": []}
    db = SessionLocal()
    try:
        seeds = [
            {
                "username": settings.SEED_ADMIN_USERNAME,
                "password": settings.SEED_ADMIN_PASSWORD,
                "role":     "ADMIN",
            },
            {
                "username": settings.SEED_USER_USERNAME,
                "password": settings.SEED_USER_PASSWORD,
                "role":     "USER",
            },
        ]
        for seed in seeds:
            existing = db.query(User).filter(User.username == seed["username"]).first()
            if existing:
                result["existing"].append(seed["username"])
                continue
            db.add(User(
                id=str(uuid.uuid4()),
                username=seed["username"],
                password_hash=hash_password(seed["password"]),
                role=seed["role"],
                active="true",
                approved_by="seed",
                created_at=datetime.utcnow(),
            ))
            result["created"].append(seed["username"])
        db.commit()
    except Exception as e:
        logger.error("SEED_USERS_FAILED: %s", e)
        db.rollback()
    finally:
        db.close()
    return result
