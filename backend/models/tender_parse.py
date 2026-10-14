"""招标文件解析（tender-parse）models：解析记录 + 记录-文档关联。

插件「招标文件解析」按钮（doc/招标文件解析需求.md §5）：
- 一条解析记录 = 上传一份招标文件 → 文档解析（复用 parse_document 管道）
  → AI 要素提取（六 Tab 行模型：字段名/内容/原文位置）；
- 独立轻量记录表，不强绑 Project（需求 §8 Q10 默认）；文件本体走 documents
  表草稿口径（project_id IS NULL + owner_user_id），doc_type='tender_parse'
  与检查页 'tender' 草稿隔离，不占检查页配额；
- 记录行同时是计费任务行（照 bid_wizard 任务级模式）：
  status ∈ pending/running/completed/failed 满足 finalize_task_usage 终态判定，
  stage 细化展示 queued/parsing/extracting（需求 §4.3 状态机）；
- 多文件（正文+补遗）P2 再放开（Q4 默认）：关联表先按 1..n 建好，P0 只写一行。
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .document import Document


class TenderParseRecord(Base):
    """One parse record: one uploaded tender file → structured element result."""

    __tablename__ = "tender_parse_records"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 列表页展示名：初始=上传文件名去扩展名；提取出 basic.project_name 后回填
    project_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # 招标方名称：提取完成后回填（需求 §2.1 列表列）
    tender_unit: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # pending / running / completed / failed（计费终态判定口径）
    status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default="pending", nullable=False, index=True
    )
    # queued / parsing / extracting —— 细化「解析中」具体环节（需求 §4.3）
    stage: Mapped[str] = mapped_column(
        String(40), default="queued", server_default="queued", nullable=False
    )
    # parsing / extracting：失败环节，重试从该环节续跑
    failed_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 记录创建 → 终态的总时长（需求 §4.3「耗时」）
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # AI 提取结果（六 Tab 行模型，见 tender_parse_agent.TAB_FIELDS）
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 人工修正版（编辑保存后有值，展示以修正版为准；重新解析覆盖需二次确认）
    manual_edit: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 计费任务行（照 BidWizardQaTask 列集：reconcile_task_billing 统一引用）
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

    documents: Mapped[list["TenderParseDocument"]] = relationship(
        "TenderParseDocument", back_populates="record", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TenderParseRecord(id={self.id}, status={self.status}, stage={self.stage})>"


class TenderParseDocument(Base):
    """Record ↔ Document link (P0 single row; multi-file reserved for P2)."""

    __tablename__ = "tender_parse_documents"

    record_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tender_parse_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    record: Mapped["TenderParseRecord"] = relationship("TenderParseRecord", back_populates="documents")
    document: Mapped["Document"] = relationship("Document")

    def __repr__(self) -> str:
        return f"<TenderParseDocument(record_id={self.record_id}, document_id={self.document_id})>"
