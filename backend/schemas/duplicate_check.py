"""Schemas for technical bid duplicate checking."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .review import AgentStepResponse, ReviewTaskListItem, ReviewTaskResponse


class DuplicateFindingPayload(BaseModel):
    """Validated payload emitted by a duplicate-check sub-agent."""

    check_item_name: str = Field(min_length=1, max_length=255)
    verdict: str = Field(pattern="^(reasonable|suspicious|unknown)$")
    source_basis: str = Field(
        default="unknown", pattern="^(tender|public|bidder_authored|unknown)$"
    )
    similarity_score: float = Field(ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    coverage_status: str = Field(
        default="insufficient", pattern="^(complete|partial|insufficient)$"
    )
    channel_scores: dict[str, Any] | None = None
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
    verdict: str = Field(pattern="^(reasonable|suspicious|unknown)$")
    source_basis: str = Field(
        default="unknown", pattern="^(tender|public|bidder_authored|unknown)$"
    )
    similarity_score: float
    confidence: float | None = None
    coverage_status: str = Field(
        default="insufficient", pattern="^(complete|partial|insufficient)$"
    )
    channel_scores: dict[str, Any] | None = None
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
    unknown_count: int = 0
    coverage_status: str = "insufficient"
    coverage_warnings: list[str] = Field(default_factory=list)


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


class DuplicateCoverageDocumentResponse(BaseModel):
    document_id: str
    filename: str
    doc_type: str
    status: str
    coverage_status: str
    coverage_summary: dict | None = None


class DuplicateCoverageResponse(BaseModel):
    task_id: str
    mode: str = Field(pattern="^(pair|batch)$")
    coverage_status: str = Field(pattern="^(complete|partial|insufficient)$")
    coverage_warnings: list[str] = Field(default_factory=list)
    algorithm_version: str | None = None
    feature_snapshot: dict | None = None
    documents: list[DuplicateCoverageDocumentResponse] = Field(default_factory=list)


class DuplicateMemberResponse(BaseModel):
    task_id: str
    document_id: str
    party_key: str
    display_name: str
    ordinal: int
    metadata: dict | None = None
    filename: str | None = None
    status: str | None = None
    coverage_status: str | None = None


class DuplicatePairSummaryResponse(BaseModel):
    id: str
    task_id: str
    left_document_id: str
    right_document_id: str
    left_display_name: str | None = None
    right_display_name: str | None = None
    candidate_count: int
    finding_count: int
    suspicious_count: int
    unknown_count: int
    max_evidence_strength: float | None = None
    coverage_status: str
    channel_hits: dict | None = None


class DuplicateOccurrenceResponse(BaseModel):
    id: str
    task_id: str
    finding_id: str | None = None
    cluster_id: str | None = None
    document_id: str
    filename: str | None = None
    display_name: str | None = None
    block_id: str | None = None
    excerpt: str
    location: dict
    channel: str | None = None


class DuplicateClusterResponse(BaseModel):
    id: str
    task_id: str
    finding_id: str | None = None
    cluster_key: str
    content_type: str
    document_ids: list[str]
    occurrence_count: int
    representative_excerpt: str
    evidence_strength: float | None = None
    coverage_status: str
    metadata: dict | None = None
    occurrences: list[DuplicateOccurrenceResponse] = Field(default_factory=list)


class DuplicateMatrixResponse(BaseModel):
    task_id: str
    mode: str = Field(pattern="^(pair|batch)$")
    coverage_status: str
    coverage_warnings: list[str] = Field(default_factory=list)
    members: list[DuplicateMemberResponse] = Field(default_factory=list)
    pairs: list[DuplicatePairSummaryResponse] = Field(default_factory=list)


__all__ = [
    "AgentStepResponse",
    "DuplicateFindingPayload",
    "DuplicateResultResponse",
    "DuplicateResultsResponse",
    "DuplicateSummary",
    "DuplicateTodoResponse",
    "DuplicateCoverageDocumentResponse",
    "DuplicateCoverageResponse",
    "DuplicateMemberResponse",
    "DuplicatePairSummaryResponse",
    "DuplicateOccurrenceResponse",
    "DuplicateClusterResponse",
    "DuplicateMatrixResponse",
    "ReviewTaskListItem",
    "ReviewTaskResponse",
]
