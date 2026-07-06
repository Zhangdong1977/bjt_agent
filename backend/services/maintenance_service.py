"""维护状态服务。

集中维护模式的读写逻辑，让 ``api/auth.py``（登录拦截）和
``api/system_status.py``（管理端切换）共用同一份事实，路由保持瘦。

- ``ensure_maintenance_row``：启动时幂等建单行种子。
- ``get_maintenance_state``：读当前态；行缺失时 fail-open（返回 disabled），
  绝不因读不到行而误拦登录。
- ``set_maintenance_state``：不可变式更新——构造新值后 UPDATE。
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import MAINTENANCE_ROW_ID, SystemMaintenance
from backend.utils.time_utils import utc_now


@dataclass(frozen=True)
class MaintenanceState:
    """维护状态的不可变快照。"""

    is_enabled: bool
    reason: str
    started_at: datetime | None
    updated_by: str | None
    updated_at: datetime | None


_DISABLED = MaintenanceState(
    is_enabled=False,
    reason="",
    started_at=None,
    updated_by=None,
    updated_at=None,
)


def _to_state(row: SystemMaintenance | None) -> MaintenanceState:
    """行缺失 → fail-open 返回 disabled，不抛错。"""
    if row is None:
        return _DISABLED
    return MaintenanceState(
        is_enabled=bool(row.is_enabled),
        reason=row.reason or "",
        started_at=row.started_at,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


async def ensure_maintenance_row(db: AsyncSession) -> None:
    """幂等：保证单行种子存在。lifespan 启动时调用。"""
    row = await db.get(SystemMaintenance, MAINTENANCE_ROW_ID)
    if row is not None:
        return
    db.add(
        SystemMaintenance(
            id=MAINTENANCE_ROW_ID,
            is_enabled=False,
            reason="",
            started_at=None,
            updated_by=None,
        )
    )
    await db.commit()


async def get_maintenance_state(db: AsyncSession) -> MaintenanceState:
    """读当前维护状态（fail-open）。供登录拦截与管理端读取共用。"""
    row = await db.get(SystemMaintenance, MAINTENANCE_ROW_ID)
    return _to_state(row)


async def set_maintenance_state(
    db: AsyncSession,
    *,
    enabled: bool,
    reason: str,
    user_id: str,
) -> MaintenanceState:
    """切换维护模式。

    - 由关闭→开启：置 ``started_at`` 为当前时刻。
    - 开启→开启：刷新 reason、保留 ``started_at``。
    - 关闭：保留 ``started_at`` 供审计（不置空），写 reason。
    - 始终写 ``updated_by``。
    """
    row = await db.get(SystemMaintenance, MAINTENANCE_ROW_ID)
    now = utc_now()
    if row is None:
        # 理论上 lifespan 已 ensure；防御性兜底。
        row = SystemMaintenance(
            id=MAINTENANCE_ROW_ID,
            is_enabled=enabled,
            reason=reason,
            started_at=now if enabled else None,
            updated_by=user_id,
        )
        db.add(row)
    else:
        turning_on = enabled and not bool(row.is_enabled)
        row.is_enabled = enabled
        row.reason = reason
        if turning_on:
            row.started_at = now
        row.updated_by = user_id
    await db.commit()
    await db.refresh(row)
    return _to_state(row)
