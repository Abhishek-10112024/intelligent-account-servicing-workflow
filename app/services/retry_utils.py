"""
retry_utils.py — Retry logic with exponential backoff for resilience.

Decorators and utilities for gracefully handling transient failures:
  - Gemini API timeouts
  - Database connection issues
  - RPS microservice failures

Uses tenacity library for robust retry mechanisms.
"""

import logging
import asyncio
from typing import Callable, TypeVar, Any
from functools import wraps

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

F = TypeVar('F', bound=Callable[..., Any])


# ── Retry decorators ──────────────────────────────────────────────────────────

def retry_on_api_error(
    max_attempts: int = 3,
    initial_wait: float = 1.0,
    max_wait: float = 10.0,
):
    """
    Retry decorator for transient API errors (timeouts, rate limits).
    
    Exponential backoff: 1s, 2s, 4s, ...
    
    Args:
        max_attempts: Max number of retry attempts
        initial_wait: Initial wait time (seconds)
        max_wait: Maximum wait time between retries (seconds)
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=initial_wait, max=max_wait),
        retry=retry_if_exception_type((TimeoutError, ConnectionError, IOError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


def retry_on_db_error(
    max_attempts: int = 2,
    initial_wait: float = 0.5,
    max_wait: float = 5.0,
):
    """
    Retry decorator for database connection errors.
    
    Args:
        max_attempts: Max number of retry attempts
        initial_wait: Initial wait time (seconds)
        max_wait: Maximum wait time between retries (seconds)
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=initial_wait, max=max_wait),
        retry=retry_if_exception_type((ConnectionError, IOError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


# ── Async retry wrapper ────────────────────────────────────────────────────────

def async_retry_on_error(
    max_attempts: int = 3,
    initial_wait: float = 1.0,
    max_wait: float = 10.0,
    backoff_factor: float = 2.0,
):
    """
    Async-compatible retry decorator with exponential backoff.
    
    Usage:
        @async_retry_on_error(max_attempts=3, initial_wait=1.0)
        async def fetch_gemini_response(...):
            ...
    """
    def decorator(func: F) -> F:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            wait_time = initial_wait
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.debug(f"Attempt {attempt}/{max_attempts} for {func.__name__}")
                    return await func(*args, **kwargs)
                
                except (TimeoutError, ConnectionError, IOError, Exception) as e:
                    last_exception = e
                    
                    if attempt < max_attempts:
                        logger.warning(
                            f"Attempt {attempt} failed for {func.__name__}: {str(e)}. "
                            f"Retrying in {wait_time}s..."
                        )
                        await asyncio.sleep(wait_time)
                        wait_time = min(wait_time * backoff_factor, max_wait)
                    else:
                        logger.error(
                            f"All {max_attempts} attempts failed for {func.__name__}: {str(e)}"
                        )
            
            raise last_exception or RuntimeError(f"{func.__name__} failed after {max_attempts} attempts")
        
        return async_wrapper
    
    return decorator


# ── Sync retry wrapper ─────────────────────────────────────────────────────────

def sync_retry_on_error(
    max_attempts: int = 3,
    initial_wait: float = 1.0,
    max_wait: float = 10.0,
    backoff_factor: float = 2.0,
):
    """
    Sync-compatible retry decorator with exponential backoff.
    
    Usage:
        @sync_retry_on_error(max_attempts=3, initial_wait=1.0)
        def validate_rps(...):
            ...
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            import time
            wait_time = initial_wait
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.debug(f"Attempt {attempt}/{max_attempts} for {func.__name__}")
                    return func(*args, **kwargs)
                
                except (TimeoutError, ConnectionError, IOError, Exception) as e:
                    last_exception = e
                    
                    if attempt < max_attempts:
                        logger.warning(
                            f"Attempt {attempt} failed for {func.__name__}: {str(e)}. "
                            f"Retrying in {wait_time}s..."
                        )
                        time.sleep(wait_time)
                        wait_time = min(wait_time * backoff_factor, max_wait)
                    else:
                        logger.error(
                            f"All {max_attempts} attempts failed for {func.__name__}: {str(e)}"
                        )
            
            raise last_exception or RuntimeError(f"{func.__name__} failed after {max_attempts} attempts")
        
        return sync_wrapper
    
    return decorator


# ── Circuit breaker pattern ────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Simple circuit breaker for rapid failure handling.
    
    States: CLOSED (normal) → OPEN (fail-fast) → HALF_OPEN (retry) → CLOSED
    
    Usage:
        gemini_breaker = CircuitBreaker(failure_threshold=5, timeout=60)
        
        @gemini_breaker.call
        async def extract_with_gemini(...):
            ...
    """
    
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
    
    def _should_attempt_reset(self) -> bool:
        """Check if timeout elapsed, allowing transition from OPEN to HALF_OPEN."""
        if self.state != "OPEN":
            return False
        
        import time
        if self.last_failure_time and (time.time() - self.last_failure_time) > self.timeout:
            logger.info("Circuit breaker transitioning to HALF_OPEN")
            self.state = "HALF_OPEN"
            return True
        
        return False
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        if self.state == "OPEN":
            if not self._should_attempt_reset():
                raise RuntimeError(f"Circuit breaker is OPEN (failed {self.failure_count} times)")
        
        try:
            result = await func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs)
            
            # Success: reset
            if self.state == "HALF_OPEN":
                logger.info("Circuit breaker transitioning to CLOSED")
                self.state = "CLOSED"
            
            self.failure_count = 0
            return result
        
        except Exception as e:
            self.failure_count += 1
            import time
            self.last_failure_time = time.time()
            
            if self.failure_count >= self.failure_threshold:
                logger.error(f"Circuit breaker opening after {self.failure_count} failures")
                self.state = "OPEN"
            
            raise e


# Global circuit breakers for critical services

gemini_breaker = CircuitBreaker(failure_threshold=5, timeout=60)
rps_breaker = CircuitBreaker(failure_threshold=3, timeout=30)
db_breaker = CircuitBreaker(failure_threshold=3, timeout=30)
