"""Structured result for one duplicate-check document pair."""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .review_task import ReviewTask


class DuplicatePairResult(Base):
    __tablename__ = "duplicate_pair_results"
    __table_args__ = (
        UniqueConstraint("todo_id", name="uq_duplicate_pair_results_todo_id"),
        UniqueConstraint(
            "task_id", "document_a_id", "document_b_id",
            name="uq_duplicate_pair_results_task_documents",
        ),
    )

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    todo_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("todo_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_a_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_b_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    conclusion: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    suspicious_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    excluded_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    matches: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rule_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    task: Mapped["ReviewTask"] = relationship("ReviewTask")
