"""Pydantic schemas for AI编标（bid-wizard）."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.agent.bid_draft_agent import OUTLINE_MAX_NODES
from backend.agent.bid_wizard_agent import GENERATION_WORD_COUNT_MAX, GENERATION_WORD_COUNT_MIN

WizardStage = Literal["material", "requirement", "outline", "writing"]
GenerationPart = Literal["business", "technical"]

# 待答问题上限：round 闸门按"未作答/待确认(supplemented)"计数——已答/已采纳/已跳过
# 的问题不算待办、不阻断再次检查（2026-09-11 反馈⑯：20 题全已采纳仍被总数顶死）。
QUESTIONNAIRE_MAX_QUESTIONS = 20
# 跨轮累计问题总数上限：已答问题保留在卡片列表（可改答），轮次多了总数会涨，
# 保存载荷与追加写入按此上限校验。
QUESTIONNAIRE_TOTAL_MAX_QUESTIONS = 60
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
    # 「额外的需求」问答历史（决策 43）：时序正序，条目 {question, answer, created_at, adopted}
    qa_history: list[Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WizardStageUpdate(BaseModel):
    stage: WizardStage


class WritingTaskBrief(BaseModel):
    """列表页「撰写中」徽标用的最新撰写任务摘要（决策 25）。"""

    id: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class WizardListItem(BaseModel):
    """项目列表页单行（§4.0）：向导 + 项目名 + 招标文件名 + 撰写状态聚合。"""

    wizard_id: str
    project_id: str
    project_name: str
    stage: str
    status: str
    tender_filename: str | None
    latest_writing_task: WritingTaskBrief | None
    updated_at: datetime
    created_at: datetime


class WizardListResponse(BaseModel):
    active: list[WizardListItem]
    archived: list[WizardListItem]


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
    """上传前预估：token 量级 + 约点数（null=价目缺失，前端退回 token 提示）。"""

    chars: int
    estimated_tokens: int
    estimated_points: int | None = None


# ------------------------------------------------------------------ questionnaire / requirements


class QuestionnaireAnswer(BaseModel):
    question_id: str = Field(min_length=1, max_length=100)
    # answered（自定义回答）/ adopted（采纳建议答案）/ skipped /
    # supplemented（该问题已补充新素材关闭，答案待下一轮 AI 检查给出）
    action: Literal["answered", "adopted", "skipped", "supplemented"]
    answer: str | None = Field(default=None, max_length=4_000)


class GenerationOptions(BaseModel):
    """生成要求（决策 38-41）：编写需求里固定的三项硬约束子结构。

    word_count 允许为空以保存草稿；必填校验在进入「编写大纲」时由 stage 端点做（决策 40/42）。
    """

    parts: list[GenerationPart] = Field(default_factory=lambda: ["technical"], min_length=1)
    word_count: int | None = Field(
        default=None, ge=GENERATION_WORD_COUNT_MIN, le=GENERATION_WORD_COUNT_MAX
    )
    charts: bool = True

    @field_validator("parts")
    @classmethod
    def _dedupe_parts(cls, value: list[str]) -> list[str]:
        deduped: list[str] = []
        for item in value:
            if item not in deduped:
                deduped.append(item)
        return deduped


class RequirementsUpdate(BaseModel):
    answers: list[QuestionnaireAnswer] = Field(min_length=0, max_length=QUESTIONNAIRE_TOTAL_MAX_QUESTIONS)
    # 不带该字段=沿用已保存的生成要求（老客户端 / 只改作答）；带则整体覆盖
    generation_options: GenerationOptions | None = None


class WizardQaAsk(BaseModel):
    """追问侧栏提问（决策 32a）。"""

    question: str = Field(min_length=1, max_length=2_000)


class WizardQaAskResponse(BaseModel):
    answer: str


class WizardQaAdopt(BaseModel):
    """采纳追问问答对并入编写需求（supplementals）。"""

    question: str = Field(min_length=1, max_length=2_000)
    answer: str = Field(min_length=1, max_length=4_000)


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


class SectionsResetWrittenResponse(BaseModel):
    """移除全部 AI 内容后服务端章节状态回退（决策 31）。"""

    reset_count: int


class WizardSectionLatestResponse(BaseModel):
    """跨任务取每个 node 的最新章节行（「重新写入 Word」批量动作的权威清单）。"""

    task_id: str
    node_id: str
    title: str
    status: str
    word_count: int | None

    model_config = ConfigDict(from_attributes=True)
