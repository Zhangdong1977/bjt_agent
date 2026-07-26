"""Cross-document evidence clusters for batch duplicate checks."""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .review_task import ReviewTask


class DuplicateEvidenceCluster(Base):
    __tablename__ = "duplicate_evidence_clusters"
    __table_args__ = (
        UniqueConstraint("task_id", "cluster_key", name="ux_duplicate_cluster_task_key"),
    )

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("duplicate_results.id", ondelete="SET NULL"), nullable=True, index=True
    )
    cluster_key: Mapped[str] = mapped_column(String(128), nullable=False)
    content_type: Mapped[str] = mapped_column(String(30), nullable=False)
    document_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    occurrence_count: Mapped[int] = mapped_column(default=0, nullable=False)
    representative_excerpt: Mapped[str] = mapped_column(nullable=False, default="")
    evidence_strength: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    coverage_status: Mapped[str] = mapped_column(String(20), default="insufficient", nullable=False)
    cluster_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    task: Mapped["ReviewTask"] = relationship("ReviewTask")
