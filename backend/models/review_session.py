"""ReviewSession model - 审查会话关联项目与规则库的检查任务批次."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ReviewSession(Base):
    """审查会话 - 关联项目与规则库的检查任务批次."""

    __tablename__ = "review_sessions"

    project_id: Mapped[str] = mapped_column(String(36), index=True)
    rule_library_path: Mapped[str] = mapped_column(String(500))
    tender_doc_path: Mapped[str] = mapped_column(String(500))
    bid_doc_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    merged_result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    total_todos: Mapped[int] = mapped_column(Integer, default=0)
    completed_todos: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<ReviewSession(id={self.id}, status={self.status})>"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "rule_library_path": self.rule_library_path,
            "tender_doc_path": self.tender_doc_path,
            "bid_doc_path": self.bid_doc_path,
            "status": self.status,
            "merged_result": self.merged_result,
            "total_todos": self.total_todos,
            "completed_todos": self.completed_todos,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }