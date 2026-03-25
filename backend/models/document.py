"""Document model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .project import Project


class Document(Base):
    """Document model - represents uploaded tender or bid documents."""

    __tablename__ = "documents"

    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'tender' (招标书) or 'bid' (应标书)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    parsed_md_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parsed_images_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)  # pending, parsing, parsed, failed
    parse_error: Mapped[str | None] = mapped_column(nullable=True)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="documents")

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, doc_type={self.doc_type}, filename={self.original_filename})>"
