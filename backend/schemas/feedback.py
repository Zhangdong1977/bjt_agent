"""Feedback schemas for request/response validation."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class FeedbackCreateRequest(BaseModel):
    """Request body for submitting feedback on a finding."""

    feedback_type: Literal["confirm", "contradict", "refine"]
    contradict_reason: Literal[
        "should_comply", "severity_too_high", "severity_too_low", "item_not_applicable",
    ] | None = None
    corrected_severity: Literal["critical", "major", "minor"] | None = None
    corrected_suggestion: str | None = None
    corrected_is_compliant: bool | None = None
    comment: str | None = None

    @model_validator(mode="after")
    def validate_fields(self) -> "FeedbackCreateRequest":
        """Ensure required fields are present based on feedback_type."""
        if self.feedback_type == "contradict" and not self.contradict_reason:
            raise ValueError("contradict 反馈必须填写反对原因")
        if self.feedback_type == "refine":
            has_correction = any([
                self.corrected_severity is not None,
                self.corrected_suggestion is not None,
                self.corrected_is_compliant is not None,
            ])
            if not has_correction:
                raise ValueError("refine 反馈必须至少修正一项（严重度/建议/合规性）")
        return self


class BatchFeedbackRequest(BaseModel):
    """Request body for batch-confirming findings."""

    rule_doc_name: str | None = None
    comment: str | None = None


class FeedbackReviewRequest(BaseModel):
    """Request body for admin reviewing a pending feedback."""

    action: Literal["accept", "reject"]
    reason: str | None = None


class BatchFeedbackReviewRequest(BaseModel):
    """Request body for admin batch-reviewing pending feedback."""

    action: Literal["accept", "reject"]
    reason: str | None = None
    task_id: str | None = None
    batch_id: str | None = None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class FeedbackResponse(BaseModel):
    """Single feedback record response."""

    id: str
    finding_id: str
    user_id: str
    project_id: str
    task_id: str
    feedback_type: str
    contradict_reason: str | None = None
    corrected_severity: str | None = None
    corrected_suggestion: str | None = None
    corrected_is_compliant: bool | None = None
    comment: str | None = None
    status: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    affected_skill_id: str | None = None
    confidence_delta: float
    batch_id: str | None = None
    rule_doc_name: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class BatchFeedbackResponse(BaseModel):
    """Response for batch feedback operation."""

    created_count: int
    superseded_count: int


class FeedbackSummaryResponse(BaseModel):
    """Aggregated feedback statistics for a project."""

    total_feedback: int
    by_type: dict[str, int]
    by_status: dict[str, int]
    agreement_rate: float
    top_contradicted_rules: list[dict]


class ConfidenceTimelinePoint(BaseModel):
    """A single point in the confidence evolution timeline."""

    date: str
    avg_confidence: float
    skill_count: int


class SkillSummary(BaseModel):
    """Summary of a single experience skill."""

    id: str
    cluster_id: str
    group_id: str
    name: str
    description: str
    skill_form: str
    confidence: float
    maturity_score: float
    feedback_count: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class DashboardResponse(BaseModel):
    """Experience dashboard aggregated metrics."""

    active_skills: int
    retired_skills: int
    total_feedback: int
    agreement_rate: float
    avg_confidence: float
    avg_maturity: float
    confidence_timeline: list[ConfidenceTimelinePoint]
    feedback_by_type: dict[str, int]
    top_contradicted_rules: list[dict]
    recent_feedback: list[FeedbackResponse]
    skills: list[SkillSummary]


class ProjectFeedbackSummary(BaseModel):
    """Single project row with aggregated feedback counts."""

    project_id: str
    project_name: str
    user_id: str
    username: str
    total_feedback: int
    reviewed_feedback: int
    unreviewed_feedback: int
    created_at: datetime
    # 项目状态维度（后端用相关子查询计算）
    is_deleted: bool
    has_documents: bool
    has_review: bool
    review_completed: bool


class PaginatedProjectSummary(BaseModel):
    """Paginated response for project feedback summary list."""

    items: list[ProjectFeedbackSummary]
    total: int
    limit: int
    offset: int


class BatchFeedbackReviewResponse(BaseModel):
    """Response for batch feedback review operation."""

    reviewed_count: int
    action: str
