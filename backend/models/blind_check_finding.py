"""Structured blind-mark compliance finding model."""

from typing import Any, TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .blind_check_task import BlindCheckTask


class BlindCheckFinding(Base):
    """A single compliance conclusion or unresolved check item."""

    __tablename__ = "blind_check_findings"

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("blind_check_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paragraph_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    rule_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)

    task: Mapped["BlindCheckTask"] = relationship("BlindCheckTask", back_populates="findings")

    def __repr__(self) -> str:
        return f"<BlindCheckFinding(id={self.id}, verdict={self.verdict}, severity={self.severity})>"
