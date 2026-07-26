"""Schemas for the S2-0 document artifact and evidence layer.

The artifact layer is deliberately independent from the duplicate verdict
schema.  It describes what the parser actually observed, so a later matching
algorithm can be replayed without changing the source document or inventing
evidence during an LLM call.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


CoverageStatus = Literal["complete", "partial", "insufficient"]


class DuplicateEvidenceBlock(BaseModel):
    """A stable, location-aware unit in the duplicate-check intermediate form."""

    block_id: str
    document_id: str
    document_role: str | None = None
    party_key: str | None = None
    content_type: str
    section_path: list[str] = Field(default_factory=list)
    page_number: int | None = None
    bbox: dict[str, float] | None = None
    start_line: int | None = None
    end_line: int | None = None

    raw_text: str = ""
    normalized_text: str = ""
    normalized_hash: str
    numbers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    units: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)

    table_id: str | None = None
    row_index: int | None = None
    column_index: int | None = None
    header_map: dict[str, str] | None = None

    image_path: str | None = None
    image_sha256: str | None = None
    perceptual_hash: str | None = None
    image_width: int | None = Field(default=None, ge=0)
    image_height: int | None = Field(default=None, ge=0)
    parent_block_id: str | None = None
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    ocr_provider: str | None = None
    ocr_error: str | None = None
    vision_description: str | None = None

    parser_name: str
    parser_version: str
    artifact_hash: str
    source_basis: str = "unknown"


class CoverageSummary(BaseModel):
    """Parser coverage facts exposed to callers and persisted on Document."""

    status: CoverageStatus
    pages_total: int | None = None
    pages_parsed: int | None = None
    page_ratio: float | None = Field(default=None, ge=0, le=1)

    text_units: int = Field(default=0, ge=0)
    text_covered_units: int = Field(default=0, ge=0)
    text_ratio: float = Field(default=0, ge=0, le=1)

    table_count: int = Field(default=0, ge=0)
    structured_table_count: int = Field(default=0, ge=0)
    table_ratio: float = Field(default=0, ge=0, le=1)

    image_count: int = Field(default=0, ge=0)
    hashed_image_count: int = Field(default=0, ge=0)
    ocr_image_count: int = Field(default=0, ge=0)
    image_hash_ratio: float = Field(default=0, ge=0, le=1)
    image_ocr_ratio: float = Field(default=0, ge=0, le=1)

    scanned_page_count: int = Field(default=0, ge=0)
    ocr_page_count: int = Field(default=0, ge=0)
    failed_ocr_page_count: int = Field(default=0, ge=0)

    unresolved_objects: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class ArtifactFile(BaseModel):
    """A parser output file without exposing an absolute filesystem path."""

    name: str
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    available: bool = False


class ArtifactSource(BaseModel):
    name: str
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    available: bool = False


class ArtifactManifest(BaseModel):
    """Versioned manifest for all deterministic parser products."""

    schema_version: str
    document_id: str
    document_role: str | None = None
    generated_at: datetime
    source: ArtifactSource
    artifacts: dict[str, ArtifactFile] = Field(default_factory=dict)
    parser_name: str
    parser_version: str
    evidence_block_count: int = Field(default=0, ge=0)
    counts: dict[str, int] = Field(default_factory=dict)
    coverage: CoverageSummary
    warnings: list[str] = Field(default_factory=list)


class DocumentArtifactsResponse(BaseModel):
    """Coverage and optional evidence blocks for a parsed document."""

    document_id: str
    manifest: ArtifactManifest | None = None
    coverage: CoverageSummary | None = None
    blocks: list[DuplicateEvidenceBlock] = Field(default_factory=list)
    block_count: int = Field(default=0, ge=0)
    truncated: bool = False


__all__ = [
    "ArtifactFile",
    "ArtifactManifest",
    "ArtifactSource",
    "CoverageStatus",
    "CoverageSummary",
    "DocumentArtifactsResponse",
    "DuplicateEvidenceBlock",
]
