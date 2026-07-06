"""System announcement models.

系统公告：内部用户发布、面向全员（含登录页访客）广播的公告。
- ``SystemAnnouncement`` 公告本体（标题、正文、严重度、上下线、过期时间）。
- ``SystemAnnouncementRead`` 每用户已读记录，``UNIQUE(announcement_id, user_id)``
  保证「标记已读」幂等。
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .user import User


# 允许的严重度取值：info 普通 / important 重要 / urgent 紧急
ANNOUNCEMENT_SEVERITIES = ("info", "important", "urgent")


class SystemAnnouncement(Base):
    """一条系统公告。"""

    __tablename__ = "system_announcements"

    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="info", server_default="info", index=True
    )
    # 是否启用（下线后不再对用户/登录页展示，但保留记录供管理端查看）
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(), index=True
    )
    # 发布时间：发布前不展示。默认与创建同步，便于按发布时间排序。
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    # 过期时间：可选。过期后不再展示，但记录保留。
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # 发布人（内部用户）
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    reads: Mapped[list["SystemAnnouncementRead"]] = relationship(
        "SystemAnnouncementRead",
        back_populates="announcement",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<SystemAnnouncement(id={self.id}, title={self.title})>"


class SystemAnnouncementRead(Base):
    """用户对某条公告的已读记录。``UNIQUE(announcement_id, user_id)`` 保证幂等。"""

    __tablename__ = "system_announcement_reads"
    __table_args__ = (
        UniqueConstraint("announcement_id", "user_id", name="uq_announcement_read_user"),
    )

    announcement_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("system_announcements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    announcement: Mapped["SystemAnnouncement"] = relationship(
        "SystemAnnouncement", back_populates="reads"
    )
    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<SystemAnnouncementRead(announcement_id={self.announcement_id}, "
            f"user_id={self.user_id})>"
        )
