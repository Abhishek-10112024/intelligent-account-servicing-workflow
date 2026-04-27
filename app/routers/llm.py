"""
llm.py — LLM diagnostics endpoints.

GET /api/llm/self-test
  - Verifies that GEMINI_API_KEY is present
  - Attempts a minimal Gemini generate_content("ping") call
  - Returns a small JSON payload suitable for quick interview smoke-testing
"""

from fastapi import APIRouter

from app.config import settings
from app.services.observability import get_logger

router = APIRouter(prefix="/api/llm", tags=["LLM"])
logger = get_logger("llm_router")


@router.get("/self-test", summary="Verify Gemini key + model works")
def llm_self_test():
    if not settings.GEMINI_API_KEY.strip():
        return {
            "ok": False,
            "mode": "mock",
            "model": settings.GEMINI_MODEL,
            "error": "GEMINI_API_KEY is empty (mock mode).",
        }

    try:
        import google.generativeai as genai

        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel(settings.GEMINI_MODEL)
        resp = model.generate_content("ping")

        text = (getattr(resp, "text", "") or "").strip()
        logger.info("LLM_SELF_TEST_OK", model=settings.GEMINI_MODEL, response_preview=text[:80])
        return {
            "ok": True,
            "mode": "gemini",
            "model": settings.GEMINI_MODEL,
            "response_preview": text[:120],
        }
    except Exception as exc:
        logger.error("LLM_SELF_TEST_FAILED", model=settings.GEMINI_MODEL, error=str(exc))
        return {
            "ok": False,
            "mode": "gemini",
            "model": settings.GEMINI_MODEL,
            "error": str(exc),
        }

