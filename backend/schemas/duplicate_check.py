"""Schemas for duplicate-check tasks and pair results."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DuplicateEvidence(BaseModel):
    section_id: str | None = None
    section_title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    excerpt: str = Field(..., min_length=1, max_length=10000)


class DuplicateMatch(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    duplicate_type: Literal["exact", "near_duplicate", "semantic_duplicate"]
    document_a_evidence: DuplicateEvidence
    document_b_evidence: DuplicateEvidence
    analysis: str = Field(..., min_length=1, max_length=5000)


class DuplicateAgentResult(BaseModel):
    conclusion: Literal[
        "suspicious_duplicate", "no_suspicious_duplicate", "manual_review_required"
    ]
    summary: str = Field(..., min_length=1, max_length=5000)
    excluded_count: int = Field(0, ge=0)
    matches: list[DuplicateMatch] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_conclusion(self):
        if self.conclusion == "suspicious_duplicate" and not self.matches:
            raise ValueError("发现可疑重复时必须提供双边证据")
        if self.conclusion == "no_suspicious_duplicate" and self.matches:
            raise ValueError("无可疑重复结论不能包含 matches")
        return self


class DuplicatePairResponse(BaseModel):
    id: str
    todo_id: str
    document_a_id: str
    document_b_id: str
    document_a_name: str | None = None
    document_b_name: str | None = None
    execution_mode: str
    conclusion: str
    summary: str | None
    suspicious_count: int
    excluded_count: int
    matches: list
    rule_name: str
    rule_version: str | None
    rule_hash: str
    created_at: datetime


class DuplicateResultsResponse(BaseModel):
    summary: dict
    pairs: list[DuplicatePairResponse]


class DuplicateTaskResponse(BaseModel):
    id: str
    project_id: str
    task_type: str
    status: str
    celery_task_id: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: int | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
