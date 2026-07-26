"""Task-level document pair matrix statistics."""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .review_task import ReviewTask
    from .document import Document


class DuplicatePairSummary(Base):
    __tablename__ = "duplicate_pair_summaries"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "left_document_id", "right_document_id",
            name="ux_duplicate_pair_summary",
        ),
    )

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    left_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    right_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    candidate_count: Mapped[int] = mapped_column(default=0, nullable=False)
    finding_count: Mapped[int] = mapped_column(default=0, nullable=False)
    suspicious_count: Mapped[int] = mapped_column(default=0, nullable=False)
    unknown_count: Mapped[int] = mapped_column(default=0, nullable=False)
    max_evidence_strength: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    coverage_status: Mapped[str] = mapped_column(String(20), default="insufficient", nullable=False)
    channel_hits: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    task: Mapped["ReviewTask"] = relationship("ReviewTask")
    left_document: Mapped["Document"] = relationship("Document", foreign_keys=[left_document_id])
    right_document: Mapped["Document"] = relationship("Document", foreign_keys=[right_document_id])
