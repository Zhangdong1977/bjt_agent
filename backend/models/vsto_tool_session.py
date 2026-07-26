"""VSTO document tool session model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .user import User


class VstoToolSession(Base):
    """Short-lived capability session owned by one logged-in user and Word instance."""

    __tablename__ = "vsto_tool_sessions"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_instance_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_revision: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", nullable=False, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return f"<VstoToolSession(id={self.id}, status={self.status})>"
