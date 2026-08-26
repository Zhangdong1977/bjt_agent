"""Pydantic schemas for VSTO-driven polish/expand/abbreviate tasks."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PolishMode = Literal["expand", "polish", "abbreviate"]


class PolishTaskCreate(BaseModel):
    mode: PolishMode
    text: str = Field(min_length=1, max_length=20_000)
    requirements: str | None = Field(default=None, max_length=2_000)
    target_length: int | None = Field(default=None, ge=50, le=20_000)


class PolishTaskResponse(BaseModel):
    id: str
    mode: str
    status: str
    result_text: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
