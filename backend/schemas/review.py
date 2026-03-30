"""Review schemas."""

from datetime import datetime
from pydantic import BaseModel


class ReviewResultResponse(BaseModel):
    """Schema for a single review result/finding."""

    id: str
    requirement_key: str
    requirement_content: str
    bid_content: str | None
    is_compliant: bool
    severity: str
    location_page: int | None
    location_line: int | None
    suggestion: str | None
    explanation: str | None

    model_config = {"from_attributes": True}


class ReviewResponse(BaseModel):
    """Schema for review response with summary and findings."""

    summary: dict
    findings: list[ReviewResultResponse]


class ReviewTaskResponse(BaseModel):
    """Schema for review task status response."""

    id: str
    project_id: str
    status: str
    celery_task_id: str | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None

    model_config = {"from_attributes": True}


class AgentStepResponse(BaseModel):
    """Schema for agent step response."""

    id: str
    step_number: int
    step_type: str
    content: str
    tool_name: str | None
    tool_args: dict | None = None
    tool_result: dict | None = None
    duration_ms: int | None

    model_config = {"from_attributes": True}


class ReviewTaskListItem(BaseModel):
    """Lightweight review task info for list display."""

    id: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
