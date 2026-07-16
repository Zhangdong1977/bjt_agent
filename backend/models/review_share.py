"""Review result share token model.

A ``ReviewShareToken`` allows a project owner to share a single review task's
results with other logged-in users. Unlike normal review endpoints (which
check ``project.user_id`` ownership), the share endpoints only require a valid
account login + a valid token — so the recipient need not be the project owner.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ReviewShareToken(Base):
    """Share token for a single review task's results."""

    __tablename__ = "review_share_tokens"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("review_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 签发给特定审查任务的可分享访问令牌；持有人登录后可只读查看该任务结果。
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # 过期时间（可空 = 永不过期）。
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    def __repr__(self) -> str:
        return f"<ReviewShareToken(token={self.token[:8]}..., task_id={self.task_id})>"
