"""Schemas for technical bid duplicate checking."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .review import AgentStepResponse, ReviewTaskListItem, ReviewTaskResponse


class DuplicateFindingPayload(BaseModel):
    """Validated payload emitted by a duplicate-check sub-agent."""

    check_item_name: str = Field(min_length=1, max_length=255)
    verdict: str = Field(pattern="^(reasonable|suspicious)$")
    similarity_score: float = Field(ge=0, le=1)
    match_type: str = Field(
        pattern="^(exact|near_exact|semantic|structural|ocr_error|logic_anomaly)$"
    )
    left_excerpt: str = Field(min_length=1)
    left_location: dict[str, Any] = Field(default_factory=dict)
    right_excerpt: str = Field(min_length=1)
    right_location: dict[str, Any] = Field(default_factory=dict)
    explanation: str = Field(min_length=1)
    suggestion: str | None = None
    evidence: dict[str, Any] | None = None


class DuplicateResultResponse(BaseModel):
    id: str
    task_id: str
    todo_id: str | None
    rule_doc_name: str
    check_item_name: str
    verdict: str
    similarity_score: float
    match_type: str
    left_document_id: str
    left_filename: str | None = None
    left_excerpt: str
    left_location: dict[str, Any]
    right_document_id: str
    right_filename: str | None = None
    right_excerpt: str
    right_location: dict[str, Any]
    explanation: str
    suggestion: str | None
    evidence: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DuplicateSummary(BaseModel):
    rule_count: int
    completed_rule_count: int
    reasonable_count: int
    suspicious_count: int


class DuplicateTodoResponse(BaseModel):
    """Public sub-agent state without the server-side rule file path."""

    id: str
    project_id: str
    session_id: str
    rule_doc_name: str
    check_items: list | None = None
    status: str
    result: dict | None = None
    error_message: str | None = None
    retry_count: int
    max_retries: int
    max_steps: int = 100
    brain_capacity: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DuplicateResultsResponse(BaseModel):
    summary: DuplicateSummary
    findings: list[DuplicateResultResponse]
    todos: list[DuplicateTodoResponse]


__all__ = [
    "AgentStepResponse",
    "DuplicateFindingPayload",
    "DuplicateResultResponse",
    "DuplicateResultsResponse",
    "DuplicateSummary",
    "DuplicateTodoResponse",
    "ReviewTaskListItem",
    "ReviewTaskResponse",
]
