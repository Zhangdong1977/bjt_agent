"""Structured result produced by one bid-duplicate-check sub-agent."""

from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .review_task import ReviewTask


class DuplicateResult(Base):
    """One A/B evidence pair and its rule-based duplicate verdict."""

    __tablename__ = "duplicate_results"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('reasonable', 'suspicious')",
            name="ck_duplicate_results_verdict",
        ),
        CheckConstraint(
            "similarity_score >= 0 AND similarity_score <= 1",
            name="ck_duplicate_results_similarity",
        ),
    )

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    todo_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("todo_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rule_doc_name: Mapped[str] = mapped_column(String(255), nullable=False)
    check_item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    verdict: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    similarity_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    match_type: Mapped[str] = mapped_column(String(30), nullable=False)

    left_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    left_excerpt: Mapped[str] = mapped_column(nullable=False)
    left_location: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    right_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    right_excerpt: Mapped[str] = mapped_column(nullable=False)
    right_location: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    explanation: Mapped[str] = mapped_column(nullable=False)
    suggestion: Mapped[str | None] = mapped_column(nullable=True)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    task: Mapped["ReviewTask"] = relationship("ReviewTask", back_populates="duplicate_results")
