"""Blind-mark compliance check task model."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .user import User
    from .vsto_tool_session import VstoToolSession
    from .blind_check_finding import BlindCheckFinding


class BlindCheckTask(Base):
    """One asynchronous compliance check for the active Word document."""

    __tablename__ = "blind_check_tasks"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("vsto_tool_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    document_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_revision: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    scope: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(40), default="created", server_default="created", nullable=False, index=True
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User")
    tool_session: Mapped["VstoToolSession"] = relationship("VstoToolSession")
    findings: Mapped[list["BlindCheckFinding"]] = relationship(
        "BlindCheckFinding", back_populates="task", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<BlindCheckTask(id={self.id}, status={self.status})>"
