"""Polish/expand/abbreviate task model (VSTO 扩写润色)."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .user import User


class PolishTask(Base):
    """One short text-polishing run driven from the VSTO task pane."""

    __tablename__ = "polish_tasks"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # expand / polish / abbreviate
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default="pending", nullable=False, index=True
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_multiplier: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    billing_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False, index=True
    )
    billing_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    billing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return f"<PolishTask(id={self.id}, mode={self.mode}, status={self.status})>"
