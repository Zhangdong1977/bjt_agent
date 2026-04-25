"""Review task model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .project import Project
    from .review_result import ReviewResult
    from .agent_step import AgentStep


class ReviewTask(Base):
    """Review task model - represents an async bid review task."""

    __tablename__ = "review_tasks"

    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)  # pending, running, completed, failed, cancelled
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(nullable=True, index=True)  # Track frontend heartbeat - if no heartbeat for 20+ seconds, agent will cancel

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="review_tasks")
    results: Mapped[list["ReviewResult"]] = relationship("ReviewResult", back_populates="task", cascade="all, delete-orphan")
    steps: Mapped[list["AgentStep"]] = relationship("AgentStep", back_populates="task", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<ReviewTask(id={self.id}, status={self.status})>"
