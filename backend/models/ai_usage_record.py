"""AI usage record model — 每次 LLM/OCR 调用一行的用量流水。

归属维度对齐运营台 aiCheckLogin 返回的 sys_user 字段 + 本地 Project/ReviewTask/TodoItem。
与原设计相比：session_id 与 task_id 同值（均=ReviewTask.id），合并为 task_id 一列。
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Integer, Numeric, String, Date, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AiUsageRecord(Base):
    """AI 用量流水：每次 LLM 调用或 OCR 调用一行。"""

    __tablename__ = "ai_usage_records"

    # —— 归属 ——
    external_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)  # sys_user.user_id
    local_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)     # 本地 users.id
    user_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    enterprise_name: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    interior_user: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)   # ReviewTask.id（合并原 session_id）
    todo_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # —— 调用类型 ——
    usage_type: Mapped[str] = mapped_column(String(20), nullable=False)   # llm / ocr
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # deepseek/minimax/volcengine/baidu_ocr/...
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # success/error/timeout

    # —— LLM 指标 ——
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # —— OCR 指标 ——
    ocr_calls: Mapped[int] = mapped_column(Integer, default=0)
    ocr_images: Mapped[int] = mapped_column(Integer, default=0)
    ocr_words_result_num: Mapped[int] = mapped_column(Integer, default=0)
    image_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # —— 通用 ——
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)  # OCR endpoint
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cost_cny: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)

    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<AiUsageRecord(id={self.id}, type={self.usage_type}, provider={self.provider}, status={self.status})>"
