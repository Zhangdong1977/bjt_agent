"""TodoItem model - 待办任务对应一个规则文档的检查任务."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Integer, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TodoItem(Base):
    """待办任务 - 对应一个规则文档的检查任务."""

    __tablename__ = "todo_items"

    project_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    rule_doc_path: Mapped[str] = mapped_column(String(500))
    rule_doc_name: Mapped[str] = mapped_column(String(255))
    check_items: Mapped[Optional[list]] = mapped_column(JSON, default=None, nullable=True)  # 检查项列表
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/running/completed/failed
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<TodoItem(id={self.id}, status={self.status})>"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "rule_doc_path": self.rule_doc_path,
            "rule_doc_name": self.rule_doc_name,
            "check_items": self.check_items or [],
            "status": self.status,
            "result": self.result,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }