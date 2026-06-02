"""Experience feedback SQLAlchemy model."""

from datetime import datetime

from sqlalchemy import String, Float, Boolean, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ExperienceFeedback(Base):
    """User feedback on individual review findings.

    Supports three feedback types:
    - confirm: user agrees the finding is correct
    - contradict: user disagrees with the finding
    - refine: user agrees but wants to correct details (severity, suggestion, etc.)

    Feedback drives confidence evolution of ExperienceSkills and can trigger
    Skill rewriting or retirement.
    """

    __tablename__ = "experience_feedback"

    # --- Core references ---
    finding_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_results.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True,
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_tasks.id"), nullable=False, index=True,
    )

    # --- Feedback type ---
    feedback_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="confirm | contradict | refine",
    )

    # --- Contradict-specific fields ---
    contradict_reason: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="should_comply | severity_too_high | severity_too_low | item_not_applicable",
    )

    # --- Refine-specific correction fields ---
    corrected_severity: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="critical | major | minor",
    )
    corrected_suggestion: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    corrected_is_compliant: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True,
    )

    # --- Common ---
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Processing pipeline ---
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
        comment="pending | accepted | rejected | superseded",
    )
    reviewed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # --- Experience linkage (filled by processing pipeline) ---
    affected_skill_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True,
        comment="References experience_skills.id once available",
    )
    confidence_delta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # --- Batch tracking ---
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rule_doc_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        comment="Denormalized from finding for efficient queries",
    )

    def __repr__(self) -> str:
        return (
            f"<ExperienceFeedback(id={self.id}, type={self.feedback_type}, "
            f"finding={self.finding_id}, status={self.status})>"
        )
