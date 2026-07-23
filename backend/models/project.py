"""Project model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, ForeignKey, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .user import User
    from .document import Document
    from .review_task import ReviewTask
    from .project_review_result import ProjectReviewResult


class Project(Base):
    """Project model - represents a bid review project."""

    __tablename__ = "projects"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    project_type: Mapped[str] = mapped_column(
        String(20), default="review", server_default="review", nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="projects")
    documents: Mapped[list["Document"]] = relationship("Document", back_populates="project", cascade="all, delete-orphan")
    review_tasks: Mapped[list["ReviewTask"]] = relationship("ReviewTask", back_populates="project", cascade="all, delete-orphan")
    project_review_results: Mapped[list["ProjectReviewResult"]] = relationship("ProjectReviewResult", back_populates="project", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Project(id={self.id}, name={self.name})>"
