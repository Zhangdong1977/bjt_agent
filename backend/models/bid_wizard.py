"""AI编标（bid-wizard）models：向导 / 素材 / 索引任务 / 问答微任务 / 撰写任务 / 章节 / 白名单。

与 V1 bid-draft 完全独立的表族（doc/workspace/20-bid-wizard.md §5.1）；
计费字段照抄 blind_check_task 的任务级模式。
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .user import User
    from .project import Project
    from .document import Document


class BidWizard(Base):
    """One four-stage wizard (素材准备→需求确认→编写大纲→逐章撰写) per project."""

    __tablename__ = "bid_wizards"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # material / requirement / outline / writing
    stage: Mapped[str] = mapped_column(
        String(40), default="material", server_default="material", nullable=False
    )
    # active / completed / archived — one active wizard per project (partial unique index)
    status: Mapped[str] = mapped_column(
        String(40), default="active", server_default="active", nullable=False
    )
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    questionnaire: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    requirements: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    spec: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    spec_previous: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    spec_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requirements_stale: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    spec_stale: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship("User")
    project: Mapped["Project"] = relationship("Project")
    materials: Mapped[list["BidWizardMaterial"]] = relationship(
        "BidWizardMaterial", back_populates="wizard", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<BidWizard(id={self.id}, stage={self.stage}, status={self.status})>"


class BidWizardMaterial(Base):
    """One material in the wizard's project-scoped material pool (素材池)."""

    __tablename__ = "bid_wizard_materials"

    wizard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bid_wizards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # pending / indexing / indexed / failed — document parse status lives on Document
    index_status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default="pending", nullable=False
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    index_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    wizard: Mapped["BidWizard"] = relationship("BidWizard", back_populates="materials")
    document: Mapped["Document"] = relationship("Document")

    def __repr__(self) -> str:
        return f"<BidWizardMaterial(id={self.id}, index_status={self.index_status})>"


class BidWizardIndexTask(Base):
    """Celery task row: LLM 分段索引 one material (chunks/*.md + index.md)."""

    __tablename__ = "bid_wizard_index_tasks"

    wizard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bid_wizards.id", ondelete="CASCADE"), nullable=False
    )
    material_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bid_wizard_materials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default="pending", nullable=False, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_multiplier: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    billing_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False, index=True
    )
    billing_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    billing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<BidWizardIndexTask(id={self.id}, status={self.status})>"


class BidWizardQaTask(Base):
    """Sync in-request micro task: 问卷生成 / Spec 生成 / Spec AI 修订."""

    __tablename__ = "bid_wizard_qa_tasks"

    wizard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bid_wizards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # questionnaire / spec_generate / spec_revise
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default="running", server_default="running", nullable=False, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    billing_multiplier: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    billing_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False, index=True
    )
    billing_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    billing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<BidWizardQaTask(id={self.id}, action={self.action}, status={self.status})>"


class BidWritingTask(Base):
    """Celery task row: 逐章撰写 one selected section set of a confirmed spec."""

    __tablename__ = "bid_writing_tasks"

    wizard_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bid_wizards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized for the billing settle branch (project-scoped like bid_draft).
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default="pending", nullable=False, index=True
    )
    selected_nodes: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    continue_of: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_multiplier: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    billing_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False, index=True
    )
    billing_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    billing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    wizard: Mapped["BidWizard"] = relationship("BidWizard")
    project: Mapped["Project"] = relationship("Project")
    sections: Mapped[list["BidWizardSection"]] = relationship(
        "BidWizardSection", back_populates="task", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<BidWritingTask(id={self.id}, status={self.status})>"


class BidWizardSection(Base):
    """One section of a writing task; `written` is reported by the page after Word insert."""

    __tablename__ = "bid_wizard_sections"

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bid_writing_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_plan: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    # pending / generating / generated / written / failed
    status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default="pending", nullable=False
    )
    content_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    written_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped["BidWritingTask"] = relationship("BidWritingTask", back_populates="sections")

    def __repr__(self) -> str:
        return f"<BidWizardSection(node_id={self.node_id}, status={self.status})>"


class BidWizardWhitelist(Base):
    """Feature-access whitelist row (BID_WIZARD_ACCESS_MODE=whitelist)."""

    __tablename__ = "bid_wizard_whitelist"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        return f"<BidWizardWhitelist(user_id={self.user_id})>"
