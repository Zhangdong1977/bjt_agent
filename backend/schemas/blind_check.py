"""Pydantic schemas for blind-mark compliance checks."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VstoToolSessionCreate(BaseModel):
    client_instance_id: str | None = Field(default=None, max_length=255)
    document_name: str | None = Field(default=None, max_length=500)
    document_key: str | None = Field(default=None, max_length=500)
    document_revision: str | None = Field(default=None, max_length=255)
    snapshot_id: str | None = Field(default=None, max_length=36)


class VstoToolSessionResponse(BaseModel):
    id: str
    status: str
    document_name: str | None
    document_revision: str | None
    snapshot_id: str | None
    expires_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BlindCheckScope(BaseModel):
    mode: Literal["whole_document"] = "whole_document"
    confirmed: bool = False

    @model_validator(mode="after")
    def _validate_scope(self) -> "BlindCheckScope":
        if not self.confirmed:
            raise ValueError("检查整个 Word 文档前必须由用户确认检查范围")
        return self


class BlindCheckTaskCreate(BaseModel):
    tool_session_id: str = Field(min_length=1, max_length=36)
    requirement_text: str = Field(min_length=1, max_length=50_000)
    document_name: str | None = Field(default=None, max_length=500)
    document_key: str | None = Field(default=None, max_length=500)
    document_revision: str | None = Field(default=None, max_length=255)
    scope: BlindCheckScope | None = None


class BlindCheckTaskResponse(BaseModel):
    id: str
    tool_session_id: str
    requirement_text: str
    document_name: str | None
    document_revision: str | None
    snapshot_id: str | None
    scope: BlindCheckScope | None
    status: str
    celery_task_id: str | None
    summary: dict[str, Any] | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BlindCheckFindingResponse(BaseModel):
    id: str
    task_id: str
    category: str
    severity: str
    verdict: str
    title: str
    description: str
    evidence_text: str | None
    page_number: int | None
    paragraph_index: int | None
    location: dict[str, Any] | None
    rule_reference: str | None
    confidence: float | None

    model_config = ConfigDict(from_attributes=True)


class BlindCheckResultsResponse(BaseModel):
    task_id: str
    status: str
    summary: dict[str, Any] | None
    findings: list[BlindCheckFindingResponse]


class VstoToolResultRequest(BaseModel):
    tool_session_id: str = Field(min_length=1, max_length=36)
    call_id: str = Field(min_length=1, max_length=36)
    success: bool
    data: dict[str, Any] | None = None
    # Deterministic checks return compact violation lists, but large documents
    # may still produce a bounded evidence payload.  Keep this aligned with
    # the broker's 256 KiB JSON envelope instead of truncating valid results at
    # an unrelated 200k-character limit.
    content: str | None = Field(default=None, max_length=256_000)
    error: str | None = Field(default=None, max_length=2_000)
    snapshot_id: str | None = Field(default=None, max_length=36)
