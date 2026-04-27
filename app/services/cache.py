"""
cache.py — Redis caching service for RPS lookups and Checker queue.

Provides a simple async Redis interface for caching expensive operations:
  - RPS customer lookups (Cache 1 hour)
  - Checker queue snapshots (Cache 30 seconds, invalidate on decision)

Gracefully falls back to direct DB/config lookup if Redis unavailable.
"""

import json
import logging
from typing import Any, Optional
from app.config import settings

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

logger = logging.getLogger(__name__)


class CacheManager:
    """Thread-safe async Redis cache manager with fallback."""
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self.client: Optional[redis.Redis] = None
        self.available = False
    
    async def connect(self):
        """Connect to Redis on startup."""
        if not REDIS_AVAILABLE:
            logger.warning("Redis not available; caching disabled")
            return
        
        try:
            self.client = await redis.from_url(self.redis_url, decode_responses=True)
            await self.client.ping()
            self.available = True
            logger.info("Redis connected successfully")
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}. Caching disabled.")
            self.available = False
    
    async def disconnect(self):
        """Close Redis connection on shutdown."""
        if self.client:
            await self.client.close()
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        if not self.available or not self.client:
            return None
        
        try:
            value = await self.client.get(key)
            if value:
                logger.debug(f"Cache HIT: {key}")
                return json.loads(value)
            logger.debug(f"Cache MISS: {key}")
            return None
        except Exception as e:
            logger.error(f"Cache GET error for {key}: {e}")
            return None
    
    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set value in cache with TTL."""
        if not self.available or not self.client:
            return False
        
        try:
            await self.client.setex(
                key,
                ttl,
                json.dumps(value)
            )
            logger.debug(f"Cache SET: {key} (TTL: {ttl}s)")
            return True
        except Exception as e:
            logger.error(f"Cache SET error for {key}: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if not self.available or not self.client:
            return False
        
        try:
            await self.client.delete(key)
            logger.debug(f"Cache DELETE: {key}")
            return True
        except Exception as e:
            logger.error(f"Cache DELETE error for {key}: {e}")
            return False
    
    async def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all keys matching pattern."""
        if not self.available or not self.client:
            return 0
        
        try:
            keys = await self.client.keys(pattern)
            if keys:
                count = await self.client.delete(*keys)
                logger.debug(f"Cache INVALIDATE: {count} keys matching {pattern}")
                return count
            return 0
        except Exception as e:
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
