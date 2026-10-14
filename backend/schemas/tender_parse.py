"""Pydantic schemas for 招标文件解析（tender-parse）."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from backend.agent.tender_parse_agent import TAB_ORDER

TenderParseStatus = Literal["pending", "running", "completed", "failed"]

# 人工修正载荷上限：六 Tab × 每行三字段；防异常大 payload 撑爆 JSON 列
_MANUAL_EDIT_MAX_ROWS = 400


class TenderParseRow(BaseModel):
    field: str = ""
    value: str = ""
    source: str = ""


class TenderParseDocumentItem(BaseModel):
    document_id: str
    original_filename: str
    file_size: int | None = None
    page_count: int | None = None
    word_count: int | None = None
    doc_status: str | None = None


class TenderParseRecordBase(BaseModel):
    id: str
    project_name: str
    tender_unit: str | None
    status: TenderParseStatus
    stage: str
    failed_stage: str | None = None
    error_message: str | None = None
    duration_seconds: int | None = None
    created_at: datetime
    updated_at: datetime


class TenderParseListItem(TenderParseRecordBase):
    file_name: str | None = None
    file_size: int | None = None

    model_config = {"from_attributes": True}


class TenderParseListResponse(BaseModel):
    items: list[TenderParseListItem]
    total: int
    page: int
    page_size: int


class TenderParseRecordResponse(TenderParseRecordBase):
    result: dict[str, list[dict[str, str]]] | None = None
    manual_edit: dict[str, list[dict[str, str]]] | None = None
    edited_at: datetime | None = None
    documents: list[TenderParseDocumentItem] = []

    model_config = {"from_attributes": True}


class TenderParseCreateResponse(BaseModel):
    record: TenderParseRecordResponse


class TenderParseManualEdit(BaseModel):
    """人工修正载荷：六 Tab 行模型（内容可改，字段名/原文位置只读由前端控制）."""

    result: dict[str, list[TenderParseRow]] = Field(default_factory=dict)

    @field_validator("result")
    @classmethod
    def _validate_tabs(cls, value: dict[str, list[TenderParseRow]]) -> dict[str, list[TenderParseRow]]:
        total_rows = 0
        for tab, rows in value.items():
            if tab not in TAB_ORDER:
                raise ValueError(f"未知页签分组：{tab}")
            if not isinstance(rows, list):
                raise ValueError(f"分组 {tab} 必须是行数组")
            total_rows += len(rows)
            if total_rows > _MANUAL_EDIT_MAX_ROWS:
                raise ValueError("修正内容行数过多")
        return value

    def as_plain_dict(self) -> dict[str, list[dict[str, str]]]:
        return {
            tab: [row.model_dump() for row in rows]
            for tab, rows in self.result.items()
            if tab in TAB_ORDER
        }


class TenderParseStatusSummary(BaseModel):
    status: str
    stage: str
    duration_seconds: int | None = None
    error_message: str | None = None
