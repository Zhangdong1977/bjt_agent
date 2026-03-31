"""Agent step model."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .review_task import ReviewTask


class AgentStep(Base):
    """Agent step model - stores agent execution steps for timeline display.

    Step numbering follows Mini-Agent pattern:
    - Each LLM response (assistant message) = one step_number
    - tool_calls are embedded within the step, not assigned separate numbers
    - Multiple tool_calls in one LLM response share the same step_number
    """

    __tablename__ = "agent_steps"

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)  # thought, tool_call, tool_result, final
    content: Mapped[str] = mapped_column(nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_args: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tool_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    task: Mapped["ReviewTask"] = relationship("ReviewTask", back_populates="steps")

    def __repr__(self) -> str:
        return f"<AgentStep(id={self.id}, step_number={self.step_number}, step_type={self.step_type})>"
