"""Rate limiting middleware and utilities."""

from urllib.parse import urlparse, parse_qs, urlencode

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


def _force_resp2(redis_url: str) -> str:
    """给 Redis URL 追加 protocol=2（若未显式指定），兼容已有 query 参数。

    redis-py 8.x 的 from_url 把 querystring 全部透传给 Connection。limits 库的
    RedisStorage 也走 from_url，故 storage_uri 上的 protocol 会被识别。
    """
    parsed = urlparse(redis_url)
    qs = parse_qs(parsed.query)
    if "protocol" not in qs:
        qs["protocol"] = ["2"]
    new_query = urlencode(qs, doseq=True)
    return parsed._replace(query=new_query).geturl()


def create_limiter() -> Limiter:
    """Create limiter instance with Redis storage for multi-worker support.

    In production with uvicorn --workers N, each worker is a separate process.
    Using in-memory storage causes rate limits to be inconsistent across workers.
    Redis storage ensures all workers share the same rate limit counters.
    """
    from backend.config import get_settings
    settings = get_settings()

    if settings.redis_url:
        # 强制 RESP2（protocol=2）：redis-py 8.x 默认走 RESP3 会发 HELLO 命令协商，
        # Redis 6 以下（预发 4.0.11）不支持 HELLO，导致 slowapi/limits 每次请求
        # 抛 `unknown command HELLO` → captcha/send_sms 等所有限流端点 500。
        # RESP2 是全版本兼容协议，对生产 Redis 7+ 同样安全可用。
        storage_uri = _force_resp2(settings.redis_url)
        return Limiter(
            key_func=get_client_ip,
            storage_uri=storage_uri,
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
