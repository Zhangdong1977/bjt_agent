"""Project schemas."""

from datetime import datetime
from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """Schema for creating a project."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None


class ProjectUpdate(BaseModel):
    """Schema for updating a project."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None


class ProjectResponse(BaseModel):
    """Schema for project response."""

    id: str
    user_id: str
    name: str
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    """Schema for project list response."""

    projects: list[ProjectResponse]
