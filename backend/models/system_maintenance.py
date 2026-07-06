"""系统维护状态模型。

维护模式是一个**全局单例**状态：整库永远只有一行（固定 sentinel id）。
- 开启后，登录链路（``backend/api/auth.py``）对**非内部用户**返回 503
  「当前系统维护中」；内部用户仍可登录，以便把维护关掉（避免单向陷阱）。
- 只拦新登录，不影响已在线的会话。

单行而非 KV 表：维护态是单一全局开关，单行 + sentinel id 最直白，也复用
``Base`` 的 ``id/created_at/updated_at``，不与 KV 主键设计冲突。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


# 固定的单行 id：整库只有这一行维护状态。
MAINTENANCE_ROW_ID = "maintenance"


class SystemMaintenance(Base):
    """全局维护状态（单行）。id 恒为 ``MAINTENANCE_ROW_ID``。"""

    __tablename__ = "system_maintenance"

    # 让 Base 的 UUID 默认值不生效——这里强制固定 sentinel id。
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=MAINTENANCE_ROW_ID
    )
    # 是否处于维护模式（fail-closed：服务端默认 FALSE，避免裸插入误开维护）
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )
    # 维护原因（内部用户填写，展示给被拦截的登录页）
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    # 本次开启时刻；关闭后保留最近一次的值供审计
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 最近一次操作的内部用户 id
    updated_by: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )

    def __repr__(self) -> str:
        return f"<SystemMaintenance(id={self.id}, is_enabled={self.is_enabled})>"
