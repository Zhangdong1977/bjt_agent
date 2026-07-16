"""Schemas for review result sharing."""

from datetime import datetime, timezone

from pydantic import BaseModel, field_validator

from .review import ReviewResultResponse


class ShareTokenCreate(BaseModel):
    """Optional request body when creating a share token."""

    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("expires_at 必须包含时区")
        if value <= datetime.now(timezone.utc):
            raise ValueError("expires_at 必须晚于当前时间")
        return value


class ShareTokenResponse(BaseModel):
    """Response when a share token is created/retrieved."""

    token: str
    share_url: str
    project_id: str
    task_id: str
    expires_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SharedReviewPayload(BaseModel):
    """Read-only snapshot of a shared review task's results.

    Returned to any logged-in viewer holding a valid share token. Mirrors the
    shape consumed by the frontend ``ReviewResultsArea`` component.
    """

    project_id: str
    task_id: str
    project_name: str | None = None
    findings: list[ReviewResultResponse]
    todos: list["SharedTodoItemResponse"]


class SharedTodoItemResponse(BaseModel):
    """Minimum todo metadata needed to render a shared result.

    In particular, do not expose ``rule_doc_path`` or ``result.report_path``:
    both contain server-side filesystem details that a share recipient does
    not need.
    """

    id: str
    rule_doc_name: str
    check_items: list | None = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
