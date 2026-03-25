"""Review result model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Integer, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .review_task import ReviewTask


class ReviewResult(Base):
    """Review result model - represents a single non-compliance finding."""

    __tablename__ = "review_results"

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    requirement_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requirement_content: Mapped[str] = mapped_column(nullable=False)
    bid_content: Mapped[str | None] = mapped_column(nullable=True)
    is_compliant: Mapped[bool] = mapped_column(Boolean, default=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)  # critical, major, minor
    location_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(nullable=True)
    explanation: Mapped[str | None] = mapped_column(nullable=True)

    # Relationships
    task: Mapped["ReviewTask"] = relationship("ReviewTask", back_populates="results")

    def __repr__(self) -> str:
        return f"<ReviewResult(id={self.id}, severity={self.severity}, compliant={self.is_compliant})>"
