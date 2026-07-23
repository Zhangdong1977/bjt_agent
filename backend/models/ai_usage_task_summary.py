"""AI 用量任务汇总模型 — 一个审查任务一行。

由 services/usage_summary.refresh_task_summary() 从 ai_usage_records 聚合 +
JOIN review_tasks 物化而来，供运营台按任务维度展示与增量同步。
一行 = 一个 ReviewTask，与 ai_usage_records（一行一次 completion）形成一对多。

主键 task_id = ReviewTask.id = ai_usage_records.task_id。
updated_at 作为运营台增量同步游标（onupdate 自动刷新）。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AiUsageTaskSummary(Base):
    """AI 用量任务汇总：每个审查任务一行（聚合其全部 LLM/OCR 调用）。"""

    __tablename__ = "ai_usage_task_summary"

    # 主键覆盖 Base 的 id：用 task_id（= ReviewTask.id）做主键
    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # —— 任务状态维度（JOIN review_tasks）——
    task_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)  # running/completed/failed/cancelled
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # —— 归属维度（聚合 ai_usage_records）——
    external_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)  # sys_user.user_id
    local_user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    enterprise_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    interior_user: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    # —— 调用次数 ——
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    ocr_calls: Mapped[int] = mapped_column(Integer, default=0)
    failed_calls: Mapped[int] = mapped_column(Integer, default=0)

    # —— LLM token（含 DeepSeek 缓存拆分）——
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    prompt_cache_hit_tokens: Mapped[int] = mapped_column(Integer, default=0)
    prompt_cache_miss_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # —— OCR 指标 ——
    ocr_images: Mapped[int] = mapped_column(Integer, default=0)
    ocr_words_result_num: Mapped[int] = mapped_column(Integer, default=0)

    # —— 费用（写入时已按 cache 三档计好，直接 SUM）——
    cost_cny: Mapped[Optional[float]] = mapped_column(Numeric(12, 6), nullable=True)

    # —— 任务用量首末时间 ——
    first_usage_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_usage_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<AiUsageTaskSummary(task_id={self.id}, status={self.task_status}, cost={self.cost_cny})>"
