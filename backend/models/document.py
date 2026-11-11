"""Document model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Integer, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .project import Project


class Document(Base):
    """Document model - represents uploaded tender or bid documents."""

    __tablename__ = "documents"

    # project_id 可空：用户在检查页选文件时立即上传解析，此时项目尚未创建。
    # 点「开始检查」创建项目后，通过 attach 接口把草稿文档关联到项目。
    project_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # owner_user_id：草稿文档（project_id IS NULL）的归属用户；关联项目后仍保留以备审计。
    owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 上传通道：'web'（页面草稿，受草稿配额约束）/ 'api'（开放通道上传，
    # 不计入 Web 草稿配额、不可被 Web attach，提交任务时直接归入隐式项目）
    source: Mapped[str] = mapped_column(
        String(20), default="web", server_default="web", nullable=False, index=True
    )
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'tender' (招标书) or 'bid' (投标文件)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    parsed_html_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parsed_markdown_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # Markdown 文件路径
    parsed_images_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    docling_json_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # S2-0 deterministic parser artifacts.  Paths are internal workspace paths;
    # the artifact API exposes only filenames/hashes and coverage facts.
    artifact_manifest_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    evidence_blocks_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parser_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    coverage_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # S2-1 immutable source snapshot metadata.  Source documents are uploaded
    # into the workspace and their hash/version are persisted so a later
    # result can prove exactly which tender/public reference was consulted.
    source_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_snapshot_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    duplicate_party_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    duplicate_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duplicate_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)  # pending, parsing, parsed, failed
    parse_error: Mapped[str | None] = mapped_column(nullable=True)

    # Relationships
    project: Mapped["Project | None"] = relationship(
        "Project", back_populates="documents", foreign_keys=[project_id]
    )

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, doc_type={self.doc_type}, filename={self.original_filename})>"


class DocumentImageAnnotation(Base):
    """单张解析图片的预识别结果（doc 25 号方案一）。

    审查任务启动前对该项目全部已解析文档的落盘图片批量 OCR，全文与内容
    类型标签入库、跨任务复用（同一张图全库只识别一次）。标签注入
    get_section_images / doc_search 的输出，让子 agent 按图选图而非按
    文件名盲选——根治「证明材料在未识别图片中」导致的「未提供」误报。
    """

    __tablename__ = "document_image_annotations"

    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 图片文件名（basename，如 image_97.png）；(document_id, image_name) 唯一
    image_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 内容类型标签（关键词词表命中，如「会计师事务所营业执照」）；未命中为 NULL
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 首行文本摘要（无标签时的类型线索）
    summary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(nullable=True)
    provider: Mapped[str] = mapped_column(
        String(20), default="baidu", server_default="baidu", nullable=False
    )
    # ok=识别成功；failed=OCR 失败（门禁兜底场景）
    status: Mapped[str] = mapped_column(
        String(20), default="ok", server_default="ok", nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentImageAnnotation(document_id={self.document_id}, "
            f"image={self.image_name}, label={self.label}, status={self.status})>"
        )
