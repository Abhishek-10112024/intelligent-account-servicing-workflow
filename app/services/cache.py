"""
cache.py — Redis caching service for RPS lookups and Checker queue.

Provides a simple async Redis interface for caching expensive operations:
  - RPS customer lookups (cache 1 hour)
  - Checker queue snapshots (cache 30 seconds, invalidate on decision)

Gracefully falls back to direct DB/config lookup if Redis unavailable.

────────────────────────────────────────────────────────────────────────────────
DEMO-FRIENDLY REDIS LOGGING
────────────────────────────────────────────────────────────────────────────────
Every Redis operation (CONNECT, GET, SET, DELETE, INVALIDATE, PING) is logged
to a dedicated file at `logs/redis.log` with:

    timestamp | operation | key | hit/miss | ttl | latency_ms | details

This makes it trivial during a demo to tail the file and point at the exact
moment Redis is used:

    tail -f logs/redis.log

The same events are ALSO forwarded to the main application logger so they
still show up in `logs/iasw.log` and on stdout.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from app.config import settings

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

logger = logging.getLogger(__name__)


# ── Dedicated Redis activity logger ────────────────────────────────────────────
# Writes a clean, demo-friendly line for every cache op to logs/redis.log.
# Separate from the main app log so it can be tailed independently while
# presenting ("tail -f logs/redis.log" → see every cache hit/miss in real time).
def _build_redis_logger() -> logging.Logger:
    redis_logger = logging.getLogger("iasw.redis")
    redis_logger.setLevel(logging.INFO)
    # Don't bubble up to root (avoids duplicate lines in iasw.log since root
    # already has a file handler pointing there). We forward explicitly via
    # `logger.info(...)` below when we want it in both places.
    redis_logger.propagate = False

    # Avoid duplicate handlers on module reload (pytest, uvicorn --reload)
    already_wired = any(
        isinstance(h, logging.FileHandler)
        and Path(getattr(h, "baseFilename", "")) == Path(settings.REDIS_LOG_FILE)
        for h in redis_logger.handlers
    )
    if already_wired:
        return redis_logger

    settings.REDIS_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(settings.REDIS_LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-5s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    redis_logger.addHandler(file_handler)
    return redis_logger


redis_log = _build_redis_logger()


def _fmt(op: str, key: str = "-", outcome: str = "-", **extras: Any) -> str:
    """Format a Redis event line for the dedicated log."""
    # Keep columns stable so the file is easy to scan during a demo.
    extra_str = " ".join(f"{k}={v}" for k, v in extras.items() if v is not None)
    return f"{op:<10} | key={key:<40} | {outcome:<4} | {extra_str}".rstrip()


class CacheManager:
    """Thread-safe async Redis cache manager with fallback."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self.client: Optional["redis.Redis"] = None
        self.available = False

    async def connect(self):
        """Connect to Redis on startup."""
        # Banner makes it obvious in the demo log where one app session starts.
        redis_log.info("=" * 72)
        redis_log.info(f"REDIS SESSION START — url={self.redis_url}")
        redis_log.info("=" * 72)

        if not REDIS_AVAILABLE:
            redis_log.warning(_fmt("CONNECT", outcome="SKIP", reason="redis_py_not_installed"))
            logger.warning("Redis not available; caching disabled")
            return

        start = time.perf_counter()
        try:
            self.client = await redis.from_url(self.redis_url, decode_responses=True)
            await self.client.ping()
            self.available = True
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            redis_log.info(_fmt("CONNECT", outcome="OK", latency_ms=latency_ms, url=self.redis_url))
            redis_log.info(_fmt("PING", outcome="PONG", latency_ms=latency_ms))
            logger.info("Redis connected successfully")
        except Exception as e:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            redis_log.warning(
                _fmt("CONNECT", outcome="FAIL", latency_ms=latency_ms, error=str(e))
            )
            logger.warning(f"Failed to connect to Redis: {e}. Caching disabled.")
            self.available = False

    async def disconnect(self):
        """Close Redis connection on shutdown."""
        if self.client:
            await self.client.close()
            redis_log.info(_fmt("DISCONNECT", outcome="OK"))
            redis_log.info("=" * 72)
            redis_log.info("REDIS SESSION END")
            redis_log.info("=" * 72)

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        if not self.available or not self.client:
            redis_log.info(_fmt("GET", key=key, outcome="SKIP", reason="redis_unavailable"))
            return None

        start = time.perf_counter()
        try:
            value = await self.client.get(key)
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            if value:
                redis_log.info(
                    _fmt("GET", key=key, outcome="HIT",
                         latency_ms=latency_ms, bytes=len(value))
                )
                logger.debug(f"Cache HIT: {key}")
                return json.loads(value)
            redis_log.info(_fmt("GET", key=key, outcome="MISS", latency_ms=latency_ms))
            logger.debug(f"Cache MISS: {key}")
            return None
        except Exception as e:
            redis_log.error(_fmt("GET", key=key, outcome="ERR", error=str(e)))
            logger.error(f"Cache GET error for {key}: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set value in cache with TTL."""
        if not self.available or not self.client:
            redis_log.info(_fmt("SET", key=key, outcome="SKIP", reason="redis_unavailable"))
            return False

        start = time.perf_counter()
        try:
            payload = json.dumps(value)
            await self.client.setex(key, ttl, payload)
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            redis_log.info(
                _fmt("SET", key=key, outcome="OK",
                     ttl=ttl, bytes=len(payload), latency_ms=latency_ms)
            )
            logger.debug(f"Cache SET: {key} (TTL: {ttl}s)")
            return True
        except Exception as e:
            redis_log.error(_fmt("SET", key=key, outcome="ERR", error=str(e)))
            logger.error(f"Cache SET error for {key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if not self.available or not self.client:
            redis_log.info(_fmt("DELETE", key=key, outcome="SKIP", reason="redis_unavailable"))
            return False

        start = time.perf_counter()
        try:
            removed = await self.client.delete(key)
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            redis_log.info(
                _fmt("DELETE", key=key, outcome="OK",
                     removed=removed, latency_ms=latency_ms)
            )
            logger.debug(f"Cache DELETE: {key}")
            return True
        except Exception as e:
            redis_log.error(_fmt("DELETE", key=key, outcome="ERR", error=str(e)))
            logger.error(f"Cache DELETE error for {key}: {e}")
            return False

    async def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all keys matching pattern."""
        if not self.available or not self.client:
            redis_log.info(
                _fmt("INVAL", key=pattern, outcome="SKIP", reason="redis_unavailable")
            )
            return 0

        start = time.perf_counter()
        try:
            keys = await self.client.keys(pattern)
            if keys:
                count = await self.client.delete(*keys)
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
                redis_log.info(
                    _fmt("INVAL", key=pattern, outcome="OK",
                         matched=len(keys), removed=count, latency_ms=latency_ms)
                )
                logger.debug(f"Cache INVALIDATE: {count} keys matching {pattern}")
                return count
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            redis_log.info(
                _fmt("INVAL", key=pattern, outcome="MISS",
                     matched=0, latency_ms=latency_ms)
            )
            return 0
        except Exception as e:
            redis_log.error(_fmt("INVAL", key=pattern, outcome="ERR", error=str(e)))
            logger.error(f"Cache INVALIDATE error: {e}")
            return 0


# Global cache instance
cache_manager = CacheManager(
    redis_url=getattr(settings, 'REDIS_URL', 'redis://localhost:6379/0')
)


# ── Cache key patterns ────────────────────────────────────────────────────────

def rps_customer_key(customer_id: str) -> str:
    """RPS customer lookup cache key."""
    return f"rps:customer:{customer_id}"


def checker_queue_key() -> str:
    """Checker queue snapshot cache key."""
    return "checker:queue:pending"


def checker_item_key(request_id: str) -> str:
    """Single checker request detail cache key."""
    return f"checker:item:{request_id}"


# ── Cache operations ───────────────────────────────────────────────────────────

async def get_cached_rps_record(customer_id: str) -> Optional[dict]:
    """Get cached RPS record for customer."""
    return await cache_manager.get(rps_customer_key(customer_id))


async def set_cached_rps_record(customer_id: str, record: dict, ttl: int = 3600) -> bool:
    """Cache RPS record for customer (1 hour default)."""
    return await cache_manager.set(rps_customer_key(customer_id), record, ttl)


async def get_cached_checker_queue() -> Optional[list]:
    """Get cached Checker queue snapshot."""
    return await cache_manager.get(checker_queue_key())


async def set_cached_checker_queue(queue: list, ttl: int = 30) -> bool:
    """Cache Checker queue snapshot (30 seconds default)."""
    return await cache_manager.set(checker_queue_key(), queue, ttl)


async def invalidate_checker_cache() -> int:
    """Invalidate all Checker-related cache entries."""
    return await cache_manager.invalidate_pattern("checker:*")


async def invalidate_rps_cache(customer_id: str) -> bool:
    """Invalidate RPS cache for specific customer."""
    return await cache_manager.delete(rps_customer_key(customer_id))
