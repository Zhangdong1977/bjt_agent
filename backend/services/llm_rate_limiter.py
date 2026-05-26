"""Distributed LLM API rate limiter using Redis.

Limits concurrent LLM API calls across all Celery worker processes to
avoid overwhelming the external API rate limit (e.g., MiniMax RPM/TPM).

Uses a simple sliding-window counter in Redis. Each worker process
increments the counter before making an LLM call and decrements it
after the call completes (or fails).

The limiter is designed to be used as an async context manager:

    async with acquire_llm_rate_limit():
        response = await llm_client.generate(...)
"""

import asyncio
import logging
import time

from backend.config import get_settings

logger = logging.getLogger(__name__)

# Module-level singleton
_limiter: "_RedisRateLimiter | None" = None


def get_llm_rate_limiter() -> "_RedisRateLimiter":
    """Get or create the process-level rate limiter singleton."""
    global _limiter
    if _limiter is None:
        _limiter = _RedisRateLimiter()
    return _limiter


class _RedisRateLimiter:
    """Distributed rate limiter using Redis INCR/DECR with TTL.

    Tracks concurrent in-flight LLM requests across all worker processes.
    Uses a Redis key per second that auto-expires, providing a sliding window.
    """

    def __init__(self):
        settings = get_settings()
        self._redis_url = settings.redis_url
        self._max_concurrent = 30  # Max concurrent LLM calls across the cluster
        self._redis = None

    def _get_redis(self):
        """Lazy-initialize Redis connection (sync, for use in sync context)."""
        if self._redis is None:
            import redis as redis_lib
            self._redis = redis_lib.Redis.from_url(
                self._redis_url,
                max_connections=3,
                socket_timeout=3.0,
                socket_connect_timeout=3.0,
                decode_responses=True,
            )
        return self._redis

    def acquire(self, timeout: float = 60.0) -> bool:
        """Try to acquire an LLM call slot, waiting up to timeout seconds.

        Returns True if acquired, False if timed out.
        """
        r = self._get_redis()
        key = f"llm_concurrent:{int(time.time())}"
        deadline = time.time() + timeout

        while True:
            try:
                current = r.incr(key)
                if current == 1:
                    r.expire(key, 5)  # Auto-expire in 5s as safety net
                if current <= self._max_concurrent:
                    return True
                # Over limit — decrement and wait
                r.decr(key)
            except Exception as e:
                logger.warning(f"[LLMRateLimiter] Redis error during acquire: {e}")
                return True  # Fail open — don't block on Redis failure

            if time.time() >= deadline:
                logger.warning(f"[LLMRateLimiter] Timed out after {timeout}s waiting for slot")
                return False

            time.sleep(0.5)

    def release(self) -> None:
        """Release an LLM call slot."""
        try:
            r = self._get_redis()
            key = f"llm_concurrent:{int(time.time())}"
            r.decr(key)
        except Exception as e:
            logger.warning(f"[LLMRateLimiter] Redis error during release: {e}")


class acquire_llm_rate_limit:
    """Async context manager for distributed LLM rate limiting.

    Usage:
        async with acquire_llm_rate_limit():
            response = await llm_client.generate(...)
    """

    def __init__(self, timeout: float = 60.0):
        self._timeout = timeout
        self._acquired = False

    async def __aenter__(self):
        limiter = get_llm_rate_limiter()
        # Run sync Redis ops in a thread to avoid blocking the event loop
        self._acquired = await asyncio.to_thread(limiter.acquire, self._timeout)
        if not self._acquired:
            raise LLMRateLimitError("LLM rate limit: timed out waiting for slot")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._acquired:
            limiter = get_llm_rate_limiter()
            await asyncio.to_thread(limiter.release)
        return False


class LLMRateLimitError(Exception):
    """Raised when LLM rate limit is exceeded."""
    pass
