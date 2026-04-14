"""Rate limiting middleware and utilities."""

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse


def get_client_ip(request: Request) -> str:
    """Get client IP address from request, considering proxy headers."""
    # Check for X-Forwarded-For header first (when behind proxy)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the chain (original client)
        return forwarded_for.split(",")[0].strip()
    # Fall back to direct client IP
    return get_remote_address(request)


def create_limiter() -> Limiter:
    """Create limiter instance with Redis storage for multi-worker support.

    In production with uvicorn --workers N, each worker is a separate process.
    Using in-memory storage causes rate limits to be inconsistent across workers.
    Redis storage ensures all workers share the same rate limit counters.
    """
    from backend.config import get_settings
    settings = get_settings()

    if settings.redis_url:
        # Use Redis storage for multi-worker support via storage_uri
        return Limiter(
            key_func=get_client_ip,
            storage_uri=settings.redis_url,
        )
    else:
        # Fallback to in-memory storage (single worker mode)
        return Limiter(key_func=get_client_ip)


# Create limiter instance - storage initialized at module load
limiter = create_limiter()


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded errors."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"Rate limit exceeded: {exc.detail}. Please try again later."
        },
    )
