"""Normalized occurrence rows for duplicate findings and evidence clusters."""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .duplicate_result import DuplicateResult
    from .duplicate_evidence_cluster import DuplicateEvidenceCluster
    from .review_task import ReviewTask
    from .document import Document


class DuplicateOccurrence(Base):
    __tablename__ = "duplicate_occurrences"

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("duplicate_results.id", ondelete="CASCADE"), nullable=True, index=True
    )
    cluster_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("duplicate_evidence_clusters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    block_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    excerpt: Mapped[str] = mapped_column(nullable=False, default="")
    location: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    channel: Mapped[str | None] = mapped_column(String(30), nullable=True)

    finding: Mapped["DuplicateResult | None"] = relationship("DuplicateResult")
    cluster: Mapped["DuplicateEvidenceCluster | None"] = relationship("DuplicateEvidenceCluster")
    task: Mapped["ReviewTask"] = relationship("ReviewTask")
    document: Mapped["Document"] = relationship("Document")
