"""Experience feedback SQLAlchemy model."""

from datetime import datetime

from sqlalchemy import String, Float, Integer, Boolean, ForeignKey, Text, DateTime, CheckConstraint, Index, PrimaryKeyConstraint, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
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


class ExperienceCase(Base):
    __tablename__ = "experience_cases"

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_tasks.id"), nullable=False, index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True,
    )
    rule_doc_name: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True,
    )
    group_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
    )
    task_intent: Mapped[str] = mapped_column(Text, nullable=False)
    approach: Mapped[str] = mapped_column(Text, nullable=False)
    key_insight: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_score_llm: Mapped[float] = mapped_column(Float, nullable=False)
    quality_score_eval: Mapped[float] = mapped_column(Float, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_ids: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_step_count: Mapped[int] = mapped_column(Integer, nullable=False)
    compressed_step_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    __table_args__ = (
        Index("ix_experience_cases_group_quality", "group_id", text("quality_score DESC")),
        # task_id 索引已通过列级 index=True 自动生成，无需重复声明
        Index("ix_experience_cases_user_created", "user_id", text("created_at DESC")),
    )

    def __repr__(self) -> str:
        return (
            f"<ExperienceCase(id={self.id}, group={self.group_id}, "
            f"quality={self.quality_score})>"
        )


class ExperienceSkill(Base):
    __tablename__ = "experience_skills"

    cluster_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True,
    )
    group_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    skill_form: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="verified | hypothesis",
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    maturity_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    maturity_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_case_ids: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    rag_doc_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    __table_args__ = (
        CheckConstraint(
            "skill_form IN ('verified', 'hypothesis')",
            name="ck_experience_skills_skill_form",
        ),
        Index(
            "ix_experience_skills_group_retired_maturity",
            "group_id", "retired_at", text("maturity_score DESC"), text("confidence DESC"),
        ),
        # cluster_id 索引已通过列级 index=True 自动生成，无需重复声明
        Index("ix_experience_skills_skill_form", "skill_form"),
    )

    def __repr__(self) -> str:
        return (
            f"<ExperienceSkill(id={self.id}, cluster={self.cluster_id}, "
            f"form={self.skill_form}, maturity={self.maturity_score})>"
        )


class ExperienceClusterMembership(Base):
    __tablename__ = "experience_cluster_memberships"

    id: Mapped[str | None] = mapped_column(
        String(36), primary_key=False, nullable=True, default=None,
    )

    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experience_cases.id"), nullable=False,
    )
    cluster_id: Mapped[str] = mapped_column(String(255), nullable=False)
    group_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    assigned_by: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="embedding | llm",
    )
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("case_id", "cluster_id", name="pk_experience_cluster_memberships"),
        CheckConstraint(
            "assigned_by IN ('embedding', 'llm')",
            name="ck_experience_cluster_memberships_assigned_by",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ExperienceClusterMembership(case={self.case_id}, "
            f"cluster={self.cluster_id}, by={self.assigned_by})>"
        )
