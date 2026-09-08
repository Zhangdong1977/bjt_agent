"""Pydantic schemas for AI编标（bid-wizard）."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.agent.bid_draft_agent import OUTLINE_MAX_NODES

WizardStage = Literal["material", "requirement", "outline", "writing"]

QUESTIONNAIRE_MAX_QUESTIONS = 20
SPEC_INSTRUCTION_MAX_CHARS = 2_000


# ------------------------------------------------------------------ access


class WizardAccessResponse(BaseModel):
    enabled: bool
    mode: str


# ------------------------------------------------------------------ wizard


class WizardCreate(BaseModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    project_name: str | None = Field(default=None, max_length=200)


class WizardResponse(BaseModel):
    id: str
    project_id: str
    stage: str
    status: str
    analysis: dict[str, Any] | None
    questionnaire: dict[str, Any] | None
    requirements: dict[str, Any] | None
    spec: list[Any] | None
    spec_previous: list[Any] | None
    spec_confirmed_at: datetime | None
    requirements_stale: bool
    spec_stale: bool
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WizardStageUpdate(BaseModel):
    stage: WizardStage


# ------------------------------------------------------------------ materials


class WizardMaterialResponse(BaseModel):
    id: str
    document_id: str
    category: str | None
    index_status: str
    indexed_at: datetime | None
    index_error: str | None
    chunk_count: int | None
    # snapshot of the underlying document for display
    original_filename: str | None
    doc_status: str | None
    word_count: int | None
    page_count: int | None
    file_size: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WizardMaterialIndexResponse(BaseModel):
    material_id: str
    index_status: str
    chunk_count: int | None
    index_content: str | None


class WizardEstimateResponse(BaseModel):
    """上传前预估：只给量级（token 估算），计费按实际用量结算。"""

    chars: int
    estimated_tokens: int


# ------------------------------------------------------------------ questionnaire / requirements


class QuestionnaireAnswer(BaseModel):
    question_id: str = Field(min_length=1, max_length=100)
    # answered（自定义回答）/ adopted（采纳建议答案）/ skipped
    action: Literal["answered", "adopted", "skipped"]
    answer: str | None = Field(default=None, max_length=4_000)


class RequirementsUpdate(BaseModel):
    answers: list[QuestionnaireAnswer] = Field(min_length=0, max_length=QUESTIONNAIRE_MAX_QUESTIONS)


# ------------------------------------------------------------------ spec


class SpecChartPlan(BaseModel):
    type: Literal["table", "mermaid"] = "table"
    title: str = Field(min_length=1, max_length=200)
    points: str | None = Field(default=None, max_length=1_000)


class SpecNode(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    level: int = Field(default=1, ge=1, le=6)
    summary: str | None = Field(default=None, max_length=2_000)
    article_count: int = Field(default=2, ge=1, le=8)
    text_count: int = Field(default=400, ge=100, le=3_000)
    charts: list[SpecChartPlan] | None = Field(default=None, max_length=6)


class SpecUpdate(BaseModel):
    spec: list[SpecNode] = Field(min_length=1, max_length=OUTLINE_MAX_NODES)


class SpecRevise(BaseModel):
    instruction: str = Field(min_length=1, max_length=SPEC_INSTRUCTION_MAX_CHARS)


# ------------------------------------------------------------------ writing tasks


class WritingTaskCreate(BaseModel):
    node_ids: list[str] = Field(min_length=1, max_length=OUTLINE_MAX_NODES)


class WritingTaskResponse(BaseModel):
    id: str
    wizard_id: str
    project_id: str
    status: str
    selected_nodes: list[Any] | None
    summary: dict[str, Any] | None
    continue_of: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WizardSectionResponse(BaseModel):
    node_id: str
    title: str
    summary: str | None
    chart_plan: list[Any] | None
    status: str
    word_count: int | None
    written_at: datetime | None
    attempts: int
    error_message: str | None

    model_config = ConfigDict(from_attributes=True)


class WizardSectionContentResponse(BaseModel):
    node_id: str
    title: str
    status: str
    content: str | None
    word_count: int | None


class SectionWrittenResponse(BaseModel):
    node_id: str
    status: str
    written_at: datetime
