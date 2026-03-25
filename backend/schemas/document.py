"""Document schemas."""

from datetime import datetime
from pydantic import BaseModel


class DocumentResponse(BaseModel):
    """Schema for document response."""

    id: str
    project_id: str
    doc_type: str
    original_filename: str
    file_path: str
    parsed_md_path: str | None
    parsed_images_dir: str | None
    page_count: int | None
    word_count: int | None
    status: str
    parse_error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    """Schema for document list response."""

    documents: list[DocumentResponse]


class DocumentContentResponse(BaseModel):
    """Schema for document content response."""

    md_content: str
    images: list[str]
