"""Rate limiting middleware and utilities."""

import logging
from urllib.parse import urlparse, parse_qs, urlencode

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


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


def rate_limit_exceeded_handler(request: Request, _exc: RateLimitExceeded):
    """Handle rate limit exceeded errors.

    返回友好中文文案，不向用户透传 ``exc.detail``（如 "1 per 1 minute"）——
    那是限流规则的技术描述，对最终用户既无用也不友好。

    同时打一条诊断日志，便于定位"生产大面积误伤"根因：若 ``counted_ip`` 与
    ``direct_ip`` 恒为同一内网地址且 ``xff`` 为空，说明反向代理未透传
    ``X-Forwarded-For``，全站共用一个 IP 限额。
    """
    counted_ip = get_client_ip(request)
    direct_ip = request.client.host if request.client else None
    logger.info(
        "[RateLimit] blocked path=%s counted_ip=%s direct_ip=%s xff=%s",
        request.url.path,
        counted_ip,
        direct_ip,
        request.headers.get("X-Forwarded-For"),
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "操作过于频繁，请稍后再试"},
    )
