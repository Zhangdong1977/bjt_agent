"""API key model (open API channel).

开放通道（``/api/v1/open``）的鉴权主体：一个 key 绑定一个 bjt-agent 用户，
任务/计费/审计均归属该用户。服务端只保存 key 的 sha256 哈希与可用于展示的
前缀；明文 key 仅在签发时返回一次。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ApiKey(Base):
    """API key for third-party open-channel clients (e.g. WorkBuddy skill)."""

    __tablename__ = "api_keys"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), default="default", nullable=False)
    # 展示用前缀（如 bjt_live_abc12345…），用于运营台/日志里辨识 key
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # 明文 key 的 sha256 hex；查询走唯一索引
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # 该 key 名下的未结算任务并发上限（覆盖账号级 billing_max_active_tasks_per_user）
    max_active_tasks: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<ApiKey(id={self.id}, prefix={self.key_prefix}, user_id={self.user_id})>"
