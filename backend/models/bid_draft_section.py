"""Generated section of one bid draft task."""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .bid_draft_task import BidDraftTask


class BidDraftSection(Base):
    """One outline node's generated markdown, persisted for regenerate/resume."""

    __tablename__ = "bid_draft_sections"
    __table_args__ = (
        UniqueConstraint("task_id", "node_id", name="uq_bid_draft_section_task_node"),
    )

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bid_draft_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # pending / generating / generated / failed
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False, index=True
    )
    content_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped["BidDraftTask"] = relationship("BidDraftTask", back_populates="sections")

    def __repr__(self) -> str:
        return f"<BidDraftSection(task_id={self.task_id}, node_id={self.node_id}, status={self.status})>"
