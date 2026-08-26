"""私有云配额客户端 —— 对接"私有云后台管理系统"的次数配额接口。

私有云模式（settings.billing_mode == "private_cloud"）下：
- 任务提交前预检：GET  {base}/pc/quota/status
- 任务终态结算：POST {base}/pc/quota/consume  （AI 任务：usage_type=ai_task, times=1）
- OCR 计量上报：POST {base}/pc/quota/consume  （usage_type=ocr, ref_id=uuid）

鉴权复用 X-Internal-Token（= settings.operate_internal_token，与私有云后台
`pc.internal-token` 同值）。base_url 复用 settings.operate_api_base_url
（私有云部署时指向私有云后台管理系统，登录 /aiCheckLogin 同源）。

契约详见 biddocument 工作树 doc/workspace/16-private-cloud.md §3.3。
"""

import logging
import time
import uuid
from typing import Any, Optional

import httpx

from backend.config import get_settings

logger = logging.getLogger(__name__)

# 预检结果短缓存（进程级）：挡住任务提交页轮询与并发打点
_status_cache: dict[str, Any] = {"at": 0.0, "data": None}


class QuotaExhausted(Exception):
    """配额耗尽/授权无效（预检失败，禁止启动任务）。"""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def is_private_cloud() -> bool:
    return get_settings().billing_mode == "private_cloud"


def private_cloud_forbidden(feature: str):
    """装饰器：私有云模式下禁用被装饰的端点（短信/充值/优惠券等公有云专属流程）。

    放在 @router.post 之上：FastAPI 经 __wrapped__ 仍能取得原函数签名。
    """

    import functools

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if is_private_cloud():
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=403,
                    detail=f"企业私有云版本不支持「{feature}」，请联系企业管理员",
                )
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def _client(timeout: float) -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        base_url=settings.operate_api_base_url.rstrip("/"),
        timeout=timeout or settings.operate_api_timeout_seconds,
        headers={"X-Internal-Token": settings.operate_internal_token},
    )


async def fetch_quota_status(*, force: bool = False) -> dict[str, Any]:
    """只取状态不校验（钱包/看板展示用）。服务异常时抛 QuotaExhausted(QUOTA_SERVICE_ERROR)。"""
    settings = get_settings()
    now = time.monotonic()
    if (
        not force
        and _status_cache["data"] is not None
        and now - _status_cache["at"] < settings.private_cloud_quota_cache_seconds
    ):
        return _status_cache["data"]
    async with _client(settings.operate_api_timeout_seconds) as client:
        resp = await client.get("/pc/quota/status")
        resp.raise_for_status()
        payload = resp.json()
    if payload.get("code") != 200:
        raise QuotaExhausted("QUOTA_SERVICE_ERROR", f"配额服务异常: {payload.get('msg')}")
    data = payload.get("data") or {}
    _status_cache["at"] = now
    _status_cache["data"] = data
    return data


async def check_ai_quota(*, force: bool = False) -> dict[str, Any]:
    """查询配额状态（带短缓存）。返回私有云后台 data 字段 dict。

    Raises QuotaExhausted: 授权无效（QUOTA_LICENSE_INVALID）或 AI 次数耗尽
    （QUOTA_EXHAUSTED）——由 authorize_billable_task_start 转 402。
    """
    data = await fetch_quota_status(force=force)

    if not data.get("valid"):
        raise QuotaExhausted(
            "QUOTA_LICENSE_INVALID",
            str(data.get("reason") or "私有云授权无效（到期或停用），请联系管理员"),
        )
    if float(data.get("aiRemaining") or 0) <= 0:
        raise QuotaExhausted(
            "QUOTA_EXHAUSTED",
            "AI 服务次数已用尽，请联系管理员扩容",
            aiRemaining=data.get("aiRemaining"),
            ocrRemaining=data.get("ocrRemaining"),
        )
    return data


def invalidate_quota_cache() -> None:
    _status_cache["at"] = 0.0
    _status_cache["data"] = None


async def consume_ai_task(
    *,
    ref_id: str,
    service_type: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    cost_cny: float = 0.0,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
) -> dict[str, Any]:
    """AI 任务结算上报（ref_id=bjt 任务ID，私有云侧幂等）。失败抛异常（结算保持 retry）。"""
    body = {
        "refId": ref_id,
        "usageType": "ai_task",
        "serviceType": service_type,
        "times": 1,
        "promptTokens": int(prompt_tokens or 0),
        "completionTokens": int(completion_tokens or 0),
        "totalTokens": int(total_tokens or 0),
        "costCny": round(float(cost_cny or 0.0), 6),
        "userId": user_id,
        "userName": user_name,
    }
    invalidate_quota_cache()
    async with _client(get_settings().operate_api_timeout_seconds) as client:
        resp = await client.post("/pc/quota/consume", json=body)
        resp.raise_for_status()
        payload = resp.json()
    if payload.get("code") != 200:
        raise RuntimeError(f"配额结算上报失败: {payload.get('msg')}")
    return payload.get("data") or {}


async def consume_ocr(
    *,
    service_type: str,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
) -> None:
    """OCR 计量上报（每次调用 1 次，ref_id=uuid 幂等）。fire-and-forget，失败仅告警。"""
    body = {
        "refId": str(uuid.uuid4()),
        "usageType": "ocr",
        "serviceType": service_type,
        "times": 1,
        "userId": user_id,
        "userName": user_name,
    }
    try:
        async with _client(get_settings().private_cloud_ocr_report_timeout_seconds) as client:
            resp = await client.post("/pc/quota/consume", json=body)
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - 计量失败不影响主流程
        logger.warning("[quota] ocr consume report failed: %s", exc)
