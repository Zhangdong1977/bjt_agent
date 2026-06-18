"""Admin / 内部接口 — 供运营台机器对机器调用。

鉴权：静态 X-Internal-Key（与运营台共享），不复用面向人的 InteriorUser JWT。
可选 IP 白名单（usage_sync_ip_allowlist）。
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import or_, select

from backend.config import get_settings
from backend.models import async_session_factory
from backend.models.ai_usage_record import AiUsageRecord

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])
settings = get_settings()


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client else ""


async def verify_internal_key(
    request: Request,
    x_internal_key: str = Header(..., alias="X-Internal-Key"),
):
    """校验静态 API Key（+ 可选 IP 白名单）。"""
    if not settings.usage_sync_api_key:
        # 未配置 key 时拒绝（避免误开放）
        raise HTTPException(status_code=503, detail="usage_sync_api_key not configured")
    if x_internal_key != settings.usage_sync_api_key:
        raise HTTPException(status_code=401, detail="invalid internal key")
    allowlist = [ip.strip() for ip in settings.usage_sync_ip_allowlist.split(",") if ip.strip()]
    if allowlist:
        ip = _client_ip(request)
        if ip not in allowlist:
            raise HTTPException(status_code=403, detail="ip not allowed")
    return True


def _serialize(r: AiUsageRecord) -> dict:
    """序列化为运营台可消费的 dict（字段名对齐镜像表 ai_usage_record）。"""
    return {
        "id": r.id,
        "source_record_id": r.id,  # 幂等键，源端 id
        "usage_type": r.usage_type,
        "provider": r.provider,
        "model": r.model,
        "status": r.status,
        "external_user_id": r.external_user_id,  # → sys_user_id
        "local_user_id": r.local_user_id,
        "user_name": r.user_name,
        "enterprise_name": r.enterprise_name,
        "interior_user": r.interior_user,
        "project_id": r.project_id,
        "task_id": r.task_id,
        "todo_id": r.todo_id,
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "total_tokens": r.total_tokens,
        "ocr_calls": r.ocr_calls,
        "ocr_images": r.ocr_images,
        "ocr_words_result_num": r.ocr_words_result_num,
        "image_size_bytes": r.image_size_bytes,
        "latency_ms": r.latency_ms,
        "endpoint": r.endpoint,
        "error_code": r.error_code,
        "error_message": r.error_message,
        "raw_usage": r.raw_usage,
        "cost_cny": float(r.cost_cny) if r.cost_cny is not None else None,
        "usage_date": r.usage_date.isoformat() if r.usage_date else None,
        "source_created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/usage/records", dependencies=[Depends(verify_internal_key)])
async def list_usage_records(
    cursor: Optional[str] = Query(None, description='格式 "{created_at_iso}|{id}"'),
    limit: int = Query(500, le=1000),
    provider: Optional[str] = None,
):
    """增量拉取用量流水。游标 = created_at + id 复合（UUID 非严格单调）。"""
    stmt = select(AiUsageRecord).order_by(AiUsageRecord.created_at, AiUsageRecord.id).limit(limit)
    if cursor:
        try:
            since_ts_str, since_id = cursor.split("|")
            since_ts = datetime.fromisoformat(since_ts_str)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="invalid cursor format")
        stmt = stmt.where(
            or_(
                AiUsageRecord.created_at > since_ts,
                (AiUsageRecord.created_at == since_ts) & (AiUsageRecord.id > since_id),
            )
        )
    if provider:
        stmt = stmt.where(AiUsageRecord.provider == provider)

    async with async_session_factory() as db:
        rows = (await db.execute(stmt)).scalars().all()

    items = [_serialize(r) for r in rows]
    next_cursor = None
    if len(items) == limit:
        last = rows[-1]
        next_cursor = f"{last.created_at.isoformat()}|{last.id}"
    return {"records": items, "next_cursor": next_cursor}
