"""Document schemas."""

from datetime import datetime
from pydantic import BaseModel, Field

from .document_artifacts import (
    ArtifactManifest,
    CoverageSummary,
    DocumentArtifactsResponse,
    DuplicateEvidenceBlock,
)


class DuplicatePairAttachRequest(BaseModel):
    """The two parsed drafts attached atomically to a duplicate project."""

    left_document_id: str = Field(min_length=1)
    right_document_id: str = Field(min_length=1)
    source_document_ids: list[str] = Field(default_factory=list, max_length=13)


class DuplicateBatchMemberAttach(BaseModel):
    document_id: str = Field(min_length=1)
    party_key: str | None = Field(default=None, max_length=120)
    display_name: str | None = Field(default=None, max_length=255)
    ordinal: int | None = Field(default=None, ge=0, le=9)


class DuplicateBatchAttachRequest(BaseModel):
    members: list[DuplicateBatchMemberAttach] = Field(min_length=3, max_length=10)
    source_document_ids: list[str] = Field(default_factory=list, max_length=13)


class DocumentResponse(BaseModel):
    """Schema for document response."""

    id: str
    project_id: str | None
    owner_user_id: str | None
    doc_type: str
    original_filename: str
    file_path: str
    parsed_html_path: str | None
    parsed_markdown_path: str | None
    parsed_images_dir: str | None
    parser_name: str | None
    parser_version: str | None
    coverage_summary: CoverageSummary | None
    source_version: str | None = None
    source_snapshot_hash: str | None = None
    source_uri: str | None = None
    source_published_at: datetime | None = None
    source_metadata: dict | None = None
    duplicate_party_key: str | None = None
    duplicate_display_name: str | None = None
    duplicate_ordinal: int | None = None
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

    content: str  # renamed from html_content
    images: list[str]
    format: str  # "markdown" or "html"


__all__ = [
    "ArtifactManifest",
    "CoverageSummary",
    "DocumentArtifactsResponse",
    "DocumentContentResponse",
    "DocumentListResponse",
    "DocumentResponse",
    "DuplicateEvidenceBlock",
    "DuplicatePairAttachRequest",
    "DuplicateBatchAttachRequest",
    "DuplicateBatchMemberAttach",
]
