"""Schemas for review result sharing."""

from datetime import datetime
from pydantic import BaseModel

from .review import ReviewResultResponse, TodoItemResponse


class ShareTokenCreate(BaseModel):
    """Optional request body when creating a share token."""

    expires_at: datetime | None = None


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
    todos: list[TodoItemResponse]
