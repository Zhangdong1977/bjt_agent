"""Bid draft generation task model (标书生成)."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .user import User
    from .project import Project
    from .document import Document
    from .bid_draft_section import BidDraftSection


class BidDraftTask(Base):
    """One async tender-analysis -> outline -> section-generation run."""

    __tablename__ = "bid_draft_tasks"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tender_document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default="pending", nullable=False, index=True
    )
    # tender_analysis / outline / generating / assembled
    phase: Mapped[str | None] = mapped_column(String(40), nullable=True)
    analysis_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    outline: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    generation_options: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    continue_of: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
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
    project: Mapped["Project"] = relationship("Project")
    tender_document: Mapped["Document"] = relationship("Document")
    sections: Mapped[list["BidDraftSection"]] = relationship(
        "BidDraftSection", back_populates="task", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<BidDraftTask(id={self.id}, status={self.status}, phase={self.phase})>"
