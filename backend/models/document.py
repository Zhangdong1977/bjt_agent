"""Document model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Integer, ForeignKey
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
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'tender' (招标书) or 'bid' (应标书)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    parsed_html_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parsed_markdown_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # Markdown 文件路径
    parsed_images_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    docling_json_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)  # pending, parsing, parsed, failed
    parse_error: Mapped[str | None] = mapped_column(nullable=True)
    structure_quality: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    structure_index_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    structure_analysis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    project: Mapped["Project | None"] = relationship(
        "Project", back_populates="documents", foreign_keys=[project_id]
    )

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, doc_type={self.doc_type}, filename={self.original_filename})>"
