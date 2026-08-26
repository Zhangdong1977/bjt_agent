"""Pydantic schemas for bid draft generation tasks."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

OUTLINE_MAX_NODES = 60


class OutlineNode(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    level: int = Field(default=1, ge=1, le=6)
    requirement: str | None = Field(default=None, max_length=2_000)
    article_count: int = Field(default=2, ge=1, le=8)
    text_count: int = Field(default=400, ge=100, le=3_000)


class BidDraftOptions(BaseModel):
    only_sections: list[str] | None = Field(default=None, max_length=OUTLINE_MAX_NODES)
    outline_hint: str | None = Field(default=None, max_length=2_000)


class BidDraftTaskCreate(BaseModel):
    project_id: str = Field(min_length=1, max_length=36)
    tender_document_id: str = Field(min_length=1, max_length=36)
    outline: list[OutlineNode] | None = Field(default=None, max_length=OUTLINE_MAX_NODES)
    analysis: dict[str, Any] | None = None
    options: BidDraftOptions | None = None


class BidDraftTaskResponse(BaseModel):
    id: str
    project_id: str
    tender_document_id: str | None
    status: str
    phase: str | None
    analysis_result: dict[str, Any] | None
    outline: list[Any] | None
    generation_options: dict[str, Any] | None
    summary: dict[str, Any] | None
    continue_of: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BidDraftSectionResponse(BaseModel):
    node_id: str
    title: str
    status: str
    word_count: int | None
    attempts: int
    error_message: str | None

    model_config = ConfigDict(from_attributes=True)


class BidDraftSectionContentResponse(BaseModel):
    node_id: str
    title: str
    status: str
    content: str | None
    word_count: int | None


class BidDraftAssembledResponse(BaseModel):
    task_id: str
    status: str
    content: str | None
    word_count: int | None
    section_total: int
    section_generated: int
    section_failed: int
