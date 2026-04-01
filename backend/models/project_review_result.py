"""Project-level merged review result model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Integer, ForeignKey, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .project import Project


class ProjectReviewResult(Base):
    """Project-level merged review result.

    This table stores the deduplicated, merged review results across all
    historical review tasks for a project.
    """

    __tablename__ = "project_review_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requirement_content: Mapped[str] = mapped_column(Text, nullable=False)
    bid_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_compliant: Mapped[bool] = mapped_column(Boolean, default=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)  # critical, major, minor
    location_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_task_id: Mapped[str] = mapped_column(String(36), ForeignKey("review_tasks.id"), nullable=False)
    merged_from_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="project_review_results")

    def __repr__(self) -> str:
        return f"<ProjectReviewResult(id={self.id}, severity={self.severity}, merged_from={self.merged_from_count})>"
