"""
main.py — FastAPI application entry point for IASW.

Enhanced with:
  1. Async task management (202 Accepted for /api/intake)
  2. Redis caching (RPS lookups, Checker queue)
  3. Rate limiting (10 req/min per IP on /api/intake)
  4. Startup/shutdown lifecycle hooks

Run with:
  source venv/bin/activate
  uvicorn app.main:app --reload --port 8000
"""

import asyncio
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import settings
from app.database import init_db
from app.routers import intake, checker, rps, auth
from app.routers.llm import router as llm_router
from app.services.observability import get_logger
from app.services.cache import cache_manager
from app.services.async_tasks import task_manager
from app.services.auth import seed_default_users
from app.services.rate_limiter import limiter  # avoids circular import with intake.py

logger = get_logger("main")

# ── FastAPI application ───────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    description=(
        "Intelligent Account Servicing Workflow — Agentic AI system for "
        "automated document verification with Human-in-the-Loop Checker approval. "
        "Enhanced with async processing, Redis caching, and rate limiting."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS Middleware ─────────────────────────────────────────────────────────────
# In production: set ALLOWED_ORIGINS in .env / docker-compose to your actual domain.
# In local dev: defaults to React dev server (localhost:5173).
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)

# ── Rate Limiting Middleware (10 req/min per IP on intake) ──────────────────────
# limiter is imported from app.services.rate_limiter (avoids circular import with intake.py)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# ── Startup & Shutdown lifecycle ──────────────────────────────────────────────
@app.on_event("startup")
async def on_startup():
    """Initialize database, seed users, and connect to Redis cache."""
    init_db()

    # Seed admin + demo user on first boot (idempotent).
    seed_result = seed_default_users()

    # Connect to Redis (non-blocking; gracefully degrades if unavailable)
    await cache_manager.connect()
    
    logger.info(
        "IASW_STARTED",
        version=settings.APP_VERSION,
        env=settings.APP_ENV,
        llm_mode="mock" if settings.USE_MOCK_LLM else "gemini",
        db=settings.DATABASE_URL,
        cache="redis" if cache_manager.available else "disabled",
        users_seeded=seed_result["created"],
        users_existing=seed_result["existing"],
    )
    print(f"\n{'='*60}")
    print(f"  IASW Server Started  (v{settings.APP_VERSION})")
    llm_label = "MOCK (no API key)" if settings.USE_MOCK_LLM else f"Gemini ({settings.GEMINI_MODEL})"
    print(f"  LLM Mode : {llm_label}")
    cache_label = "Redis enabled" if cache_manager.available else "Redis disabled (caching offline)"
    print(f"  Cache    : {cache_label}")
    print(f"  Frontend : http://localhost:8000")
    print(f"  API Docs : http://localhost:8000/docs")
    print(f"  Checker  : http://localhost:8000/checker.html")
    print(f"{'='*60}")
    # ── Demo credentials banner ───────────────────────────────────────────────
    # These are printed every boot so they're easy to find during a demo.
    # Override with SEED_*_USERNAME / SEED_*_PASSWORD env vars in production,
    # or disable seeding entirely by rotating these credentials after first boot.
    print("  DEMO CREDENTIALS (change in production!):")
    print(f"    Admin : {settings.SEED_ADMIN_USERNAME} / {settings.SEED_ADMIN_PASSWORD}")
    print(f"    User  : {settings.SEED_USER_USERNAME} / {settings.SEED_USER_PASSWORD}")
    print(f"{'='*60}\n")


@app.on_event("shutdown")
async def on_shutdown():
    """Close Redis connection on shutdown."""
    await cache_manager.disconnect()
    logger.info("IASW_SHUTDOWN")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(intake.router)
app.include_router(checker.router)
app.include_router(rps.router)
app.include_router(llm_router)



# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health():
    """System health check including cache status."""
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "llm_mode": "mock" if settings.USE_MOCK_LLM else "gemini",
        "cache": "connected" if cache_manager.available else "disconnected",
        "timestamp": asyncio.get_event_loop().time(),
    }


# ── Async Task Status Endpoints ────────────────────────────────────────────────
@app.get("/api/tasks/{task_id}", tags=["Tasks"])
async def get_task_status(task_id: str):
    """Get status of an async pipeline task."""
    task = await task_manager.get_task(task_id)
    if not task:
        return {"error": "Task not found", "task_id": task_id}, 404
    
    return task.to_dict()


@app.get("/api/tasks", tags=["Tasks"])
async def list_tasks():
    """List all tasks (for monitoring)."""
    return {"tasks": task_manager.get_all_tasks()}
