"""Task-scoped document membership for pair and batch duplicate checks."""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .review_task import ReviewTask
    from .document import Document


class DuplicateDocumentMember(Base):
    __tablename__ = "duplicate_document_members"
    __table_args__ = (
        UniqueConstraint("task_id", "document_id", name="ux_duplicate_member_task_document"),
        UniqueConstraint("task_id", "ordinal", name="ux_duplicate_member_task_ordinal"),
    )

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    party_key: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    member_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    task: Mapped["ReviewTask"] = relationship("ReviewTask")
    document: Mapped["Document"] = relationship("Document")
