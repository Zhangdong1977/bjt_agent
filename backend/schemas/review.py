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
    rule_doc_name: str | None = None
    check_item_name: str | None = None

    model_config = {"from_attributes": True}


class ProjectReviewResultResponse(BaseModel):
    """Schema for merged project-level review result."""

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
    rule_doc_name: str | None = None
    check_item_name: str | None = None
    source_task_id: str
    merged_from_count: int

    model_config = {"from_attributes": True}


class ReviewResponse(BaseModel):
    """Schema for review response with summary and findings."""

    summary: dict
    findings: list[ReviewResultResponse]


class ReviewStartRequest(BaseModel):
    """Request body for starting a review task."""

    # 勾选的检查项大类（规则文档文件名）。None = 未指定（检查全部，兼容旧客户端）；
    # 传空数组会被 API 拒绝。
    selected_rule_docs: list[str] | None = None


class RuleDocInfo(BaseModel):
    """Schema for a check-item category (rule doc) shown in the start dialog."""

    name: str
    stem: str
    # 是否默认勾选（当前除 E001 签字盖章检查外默认全选）
    default_selected: bool


class RuleDocsResponse(BaseModel):
    """Schema for the rule-doc listing endpoint."""

    rule_docs: list[RuleDocInfo]


class ReviewTaskResponse(BaseModel):
    """Schema for review task status response."""

    id: str
    project_id: str
    task_type: str = "review"
    duplicate_mode: str = "pair"
    duplicate_algorithm_version: str | None = None
    duplicate_feature_snapshot: dict | None = None
    selected_rule_docs: list[str] | None = None
    status: str
    celery_task_id: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: int | None
    error_message: str | None

    model_config = {"from_attributes": True}


class AgentStepResponse(BaseModel):
    """Schema for agent step response."""

    id: str
    task_id: str
    todo_id: str | None = None
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
    project_id: str
    task_type: str = "review"
    duplicate_mode: str = "pair"
    duplicate_algorithm_version: str | None = None
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: int | None
    error_message: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TodoItemResponse(BaseModel):
    """Schema for a todo item (sub-agent execution unit)."""

    id: str
    project_id: str
    session_id: str
    rule_doc_path: str
    rule_doc_name: str
    check_items: list | None = None
    status: str
    result: dict | None = None
    error_message: str | None = None
    retry_count: int
    max_retries: int
    max_steps: int = 500
    brain_capacity: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
