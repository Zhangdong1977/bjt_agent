"""Open API schemas (/api/v1/open)."""

from pydantic import BaseModel, Field


class MeResponse(BaseModel):
    balance_points: float
    recharge_points: float
    gift_points: float
    limits: dict


class DocumentUploadResponse(BaseModel):
    document_id: str
    doc_type: str
    status: str


class DocumentStatusResponse(BaseModel):
    document_id: str
    doc_type: str
    status: str
    pages: int | None = None
    word_count: int | None = None
    error: str | None = None


class ReviewSubmitRequest(BaseModel):
    tender_document_ids: list[str] = Field(min_length=1)
    bid_document_ids: list[str] = Field(min_length=1)


class DuplicateSubmitRequest(BaseModel):
    left_document_id: str
    right_document_id: str


class TaskSubmitResponse(BaseModel):
    task_id: str
    service: str


class ProgressInfo(BaseModel):
    percent: int | None = None
    stage: str | None = None
    stage_label: str | None = None
    message: str | None = None
    current_step: int | None = None
    total_steps: int | None = None


class TaskStatusResponse(BaseModel):
    task_id: str
    service: str
    status: str
    billing_status: str | None = None
    progress: ProgressInfo | None = None
    error: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None


class TaskListItem(BaseModel):
    task_id: str
    service: str
    status: str
    title: str | None = None
    created_at: str | None = None


class TaskListResponse(BaseModel):
    tasks: list[TaskListItem]
