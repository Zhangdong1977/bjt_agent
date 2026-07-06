"""系统状态 API routes。

- ``GET /system-status/maintenance`` —— **公开**，登录页横幅用。
- ``GET /system-status``               —— 内部用户，一次拿全（维护态 + 概览 + 节点明细）。
- ``POST /system-status/maintenance``  —— 内部用户，切换维护模式。

「一次拿全」是为了让前端状态页每个轮询周期只打一次后端、只做一次 celery
inspect 广播。路由顺序：字面量路径 ``/maintenance`` 必须先于根 ``""`` 注册，
否则 FastAPI 不会冲突（根不是参数路径），但保持显式顺序便于阅读。
"""

import dataclasses
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import InteriorUser
from backend.models import Document, ReviewTask, get_db_session
from backend.schemas.system_status import (
    MaintenancePublicResponse,
    MaintenanceStateResponse,
    MaintenanceUpdateRequest,
    NodeInfo,
    QueueDepths,
    SystemOverview,
    SystemStatusResponse,
    WorkerInfo,
)
from backend.services.cluster_status import get_cluster_status
from backend.services.maintenance_service import (
    get_maintenance_state,
    set_maintenance_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system-status", tags=["System Status"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _public_response(state) -> MaintenancePublicResponse:
    return MaintenancePublicResponse(
        is_enabled=state.is_enabled,
        reason=state.reason,
        started_at=state.started_at,
    )


def _full_response(state) -> MaintenanceStateResponse:
    return MaintenanceStateResponse(**dataclasses.asdict(state))


async def _running_review_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count(ReviewTask.id)).where(ReviewTask.status == "running")
    )
    return int(result.scalar() or 0)


async def _parsing_document_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count(Document.id)).where(Document.status == "parsing")
    )
    return int(result.scalar() or 0)


# ---------------------------------------------------------------------------
# 字面量路径先注册
# ---------------------------------------------------------------------------


@router.get("/maintenance", response_model=MaintenancePublicResponse)
async def get_maintenance_public(
    db: AsyncSession = Depends(get_db_session),
) -> MaintenancePublicResponse:
    """公开维护态（登录页横幅）。无需登录。"""
    state = await get_maintenance_state(db)
    return _public_response(state)


@router.post("/maintenance", response_model=MaintenanceStateResponse)
async def update_maintenance(
    body: MaintenanceUpdateRequest,
    current_user: InteriorUser,
    db: AsyncSession = Depends(get_db_session),
) -> MaintenanceStateResponse:
    """切换维护模式（内部用户）。"""
    state = await set_maintenance_state(
        db,
        enabled=body.enabled,
        reason=body.reason,
        user_id=current_user.id,
    )
    logger.info(
        "维护模式已%s (reason=%r, by=%s)",
        "开启" if state.is_enabled else "关闭",
        state.reason,
        current_user.id,
    )
    return _full_response(state)


@router.get("", response_model=SystemStatusResponse)
async def get_system_status(
    current_user: InteriorUser,
    db: AsyncSession = Depends(get_db_session),
) -> SystemStatusResponse:
    """系统状态页：维护态 + 全局在途/队列概览 + 节点明细（内部用户）。"""
    maintenance = await get_maintenance_state(db)
    running_reviews = await _running_review_count(db)
    parsing_docs = await _parsing_document_count(db)
    cluster = await get_cluster_status()

    return SystemStatusResponse(
        maintenance=_full_response(maintenance),
        overview=SystemOverview(
            running_reviews=running_reviews,
            parsing_documents=parsing_docs,
            review_queue=cluster["queue_depths"]["review"],
            parser_queue=cluster["queue_depths"]["parser"],
            alive_workers=cluster["alive_workers"],
            total_workers=cluster["total_workers"],
            degraded=cluster["degraded"],
        ),
        nodes=[NodeInfo(**n) for n in cluster["nodes"]],
        workers=[WorkerInfo(**w) for w in cluster["workers"]],
        queue_depths=QueueDepths(**cluster["queue_depths"]),
    )
