"""Deterministic document artifacts for duplicate-check S2-0.

This module deliberately does not score or classify duplicate findings.  It
turns parser output into a versioned, location-aware intermediate form and a
coverage manifest.  Later lexical, table, image and semantic channels can all
consume the same blocks and reproduce their input from disk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.schemas.document_artifacts import (
    ArtifactFile,
    ArtifactManifest,
    ArtifactSource,
    CoverageSummary,
    DuplicateEvidenceBlock,
)

logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA_VERSION = "duplicate-evidence/v1"
MANIFEST_SCHEMA_VERSION = "duplicate-artifact-manifest/v1"
ADAPTER_VERSION = "s2-0.1"

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_NUMBER_RE = re.compile(
    r"(?<![\w])[-+]?\d+(?:[.,]\d+)?(?:\s*(?:%|‰|万|亿))?(?![\w])"
)
_DATE_RE = re.compile(
    r"(?:\d{2,4}\s*[年/-]\s*\d{1,2}(?:\s*[月/-]\s*\d{1,2})?(?:\s*[日号])?)"
)
_UNIT_RE = re.compile(
    r"(?<![A-Za-z])(?:mm|cm|km|m|kg|g|mg|t|kw|kW|hz|Hz|mb|gb|TB|平方米|平方厘米|立方米|毫米|厘米|米|千克|公斤|吨|个|套|台|项|人|%|‰)(?![A-Za-z])",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._/-]{2,}|[A-Za-z0-9]+[-_/][A-Za-z0-9/_-]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_text(value: str | None) -> str:
    """Normalize text without discarding Chinese characters or numbers."""

    if not value:
        return ""
    value = unicodedata.normalize("NFKC", str(value))
    value = re.sub(r"\s+", " ", value).strip()
    return value.casefold()


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | None) -> str | None:
    """Return a streaming SHA-256 digest, or ``None`` for a missing file."""

    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        logger.warning("Unable to hash artifact file: %s", path, exc_info=True)
        return None
    return digest.hexdigest()


def _file_artifact(path: Path | None, *, name: str | None = None) -> ArtifactFile:
    available = bool(path and path.is_file())
    return ArtifactFile(
        name=name or (path.name if path else ""),
        sha256=sha256_file(path) if available else None,
        size_bytes=path.stat().st_size if available else None,
        available=available,
    )


def _directory_artifact(path: Path | None, *, name: str | None = None) -> ArtifactFile:
    available = bool(path and path.is_dir())
    size = None
    if available:
        try:
            size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        except OSError:
            size = None
    return ArtifactFile(
        name=name or (path.name if path else ""),
        size_bytes=size,
        available=available,
    )


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically so a parser crash cannot leave a partial artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _split_table_row(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]
    # Markdown tables use backslash escaping for literal pipes.
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in value:
        if char == "|" and not escaped:
            cells.append("".join(current).strip())
            current = []
            continue
        if char == "\\" and not escaped:
            escaped = True
            current.append(char)
            continue
        escaped = False
        current.append(char)
    cells.append("".join(current).strip())
    return cells


def _is_table_start(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and "|" in lines[index]
        and bool(_TABLE_SEPARATOR_RE.match(lines[index + 1]))
    )


def _metadata(text: str) -> dict[str, list[str]]:
    normalized = normalize_text(text)
    numbers = _unique(_NUMBER_RE.findall(normalized))
    dates = _unique(_DATE_RE.findall(normalized))
    units = _unique(_UNIT_RE.findall(normalized))
    tokens = _unique(_TOKEN_RE.findall(text))
    models = _unique(token for token in tokens if re.search(r"\d", token))
    identifiers = _unique(
        token
        for token in tokens
        if len(re.sub(r"[^A-Za-z0-9]", "", token)) >= 6
        or ("-" in token and re.search(r"\d", token))
    )
    return {
        "numbers": numbers,
        "models": models,
        "units": units,
        "dates": dates,
        "identifiers": identifiers,
    }


def _resolve_image_path(src: str, images_dir: Path | None, source_path: Path | None) -> Path | None:
    src = src.strip().strip("<>").split("#", 1)[0]
    if not src or src.startswith(("http://", "https://", "data:")):
        return None
    candidate = Path(src)
    candidates: list[Path] = []
    if candidate.is_absolute():
        candidates.append(candidate)
    if images_dir is not None:
        candidates.extend((images_dir / candidate, images_dir / candidate.name))
    if source_path is not None:
        candidates.extend((source_path.parent / candidate, source_path.parent / candidate.name))
    for item in candidates:
        if item.is_file():
            return item
    return None


def _block(
    *,
    index: int,
    document_id: str,
    document_role: str | None,
    section_path: list[str],
    content_type: str,
    raw_text: str,
    start_line: int,
    end_line: int,
    parser_name: str,
    parser_version: str,
    source_basis: str,
    **extra: Any,
) -> DuplicateEvidenceBlock:
    normalized = normalize_text(raw_text)
    metadata = _metadata(raw_text)
    computed_artifact_hash = _sha256_bytes(
        f"{content_type}\0{start_line}\0{end_line}\0{raw_text}".encode("utf-8")
    )
    artifact_hash = extra.pop("artifact_hash", computed_artifact_hash)
    return DuplicateEvidenceBlock(
        block_id=f"{document_id}:b:{index:06d}",
        document_id=document_id,
        document_role=document_role,
        party_key=document_role,
        content_type=content_type,
        section_path=list(section_path),
        start_line=start_line,
        end_line=end_line,
        raw_text=raw_text,
        normalized_text=normalized,
        normalized_hash=_sha256_bytes(normalized.encode("utf-8")),
        parser_name=parser_name,
        parser_version=parser_version,
        artifact_hash=artifact_hash,
        source_basis=source_basis,
        **metadata,
        **extra,
    )


def build_evidence_blocks(
    markdown: str,
    *,
    document_id: str,
    document_role: str | None = None,
    images_dir: Path | None = None,
    source_path: Path | None = None,
    parser_name: str = "markdown",
    parser_version: str = ADAPTER_VERSION,
    source_basis: str = "unknown",
    image_evidence: dict[str, Any] | None = None,
) -> list[DuplicateEvidenceBlock]:
    """Build deterministic paragraph, heading, table-row and image blocks."""

    lines = (markdown or "").splitlines()
    blocks: list[DuplicateEvidenceBlock] = []
    section_stack: list[str] = []
    paragraph_lines: list[str] = []
    paragraph_start = 0
    table_counter = 0

    def append_block(block: DuplicateEvidenceBlock) -> None:
        blocks.append(block)

    def flush_paragraph(end_line: int) -> None:
        nonlocal paragraph_lines, paragraph_start
        if not paragraph_lines:
            return
        raw = "\n".join(paragraph_lines).strip()
        paragraph_lines = []
        if not raw:
            return

        image_matches = list(_IMAGE_RE.finditer(raw))
        text_without_images = _IMAGE_RE.sub("", raw).strip()
        if text_without_images:
            append_block(
                _block(
                    index=len(blocks),
                    document_id=document_id,
                    document_role=document_role,
                    section_path=section_stack,
                    content_type="paragraph",
                    raw_text=text_without_images,
                    start_line=paragraph_start,
                    end_line=end_line,
                    parser_name=parser_name,
                    parser_version=parser_version,
                    source_basis=source_basis,
                )
            )

        for match in image_matches:
            alt_text, src = match.group(1).strip(), match.group(2).strip()
            image_path = _resolve_image_path(src, images_dir, source_path)
            image_digest = sha256_file(image_path)
            image_name = image_path.name if image_path else Path(src).name
            evidence_data = dict((image_evidence or {}).get(image_name) or {})
            perceptual_hash = evidence_data.get("perceptual_hash")
            image_width = evidence_data.get("width")
            image_height = evidence_data.get("height")
            if image_path is not None and (
                perceptual_hash is None or image_width is None or image_height is None
            ):
                try:
                    from backend.services.duplicate_image_evidence import (
                        image_dimensions,
                        perceptual_dhash,
                    )

                    if perceptual_hash is None:
                        perceptual_hash = perceptual_dhash(image_path)
                    if image_width is None or image_height is None:
                        image_width, image_height = image_dimensions(image_path)
                except Exception:
                    pass
            page_number = evidence_data.get("page_number")
            if page_number is None:
                page_match = re.search(r"(?:^|_)page[_-]?(\d+)|^page_(\d+)", image_name, re.I)
                if page_match:
                    page_number = int(next(group for group in page_match.groups() if group))
            image_text = alt_text or Path(src).name
            image_block = _block(
                index=len(blocks),
                document_id=document_id,
                document_role=document_role,
                section_path=section_stack,
                content_type="image",
                raw_text=image_text,
                start_line=paragraph_start,
                end_line=end_line,
                parser_name=parser_name,
                parser_version=parser_version,
                source_basis=source_basis,
                page_number=page_number,
                bbox=evidence_data.get("bbox"),
                image_path=image_name,
                image_sha256=evidence_data.get("image_sha256") or image_digest,
                perceptual_hash=perceptual_hash,
                image_width=image_width,
                image_height=image_height,
                ocr_confidence=evidence_data.get("ocr_confidence"),
                ocr_provider=evidence_data.get("ocr_provider"),
                ocr_error=evidence_data.get("ocr_error"),
                vision_description=evidence_data.get("vision_description"),
                artifact_hash=evidence_data.get("image_sha256")
                or image_digest
                or _sha256_bytes(image_text.encode("utf-8")),
            )
            append_block(image_block)
            ocr_text = str(evidence_data.get("ocr_text") or "").strip()
            if ocr_text:
                append_block(
                    _block(
                        index=len(blocks),
                        document_id=document_id,
                        document_role=document_role,
                        section_path=section_stack,
                        content_type="image_ocr",
                        raw_text=ocr_text,
                        start_line=paragraph_start,
                        end_line=end_line,
                        parser_name=parser_name,
                        parser_version=parser_version,
                        source_basis=source_basis,
                        page_number=page_number,
                        bbox=evidence_data.get("bbox"),
                        image_path=image_name,
                        image_sha256=evidence_data.get("image_sha256") or image_digest,
                        perceptual_hash=perceptual_hash,
                        image_width=image_width,
                        image_height=image_height,
                        parent_block_id=image_block.block_id,
                        ocr_confidence=evidence_data.get("ocr_confidence"),
                        ocr_provider=evidence_data.get("ocr_provider"),
                        artifact_hash=_sha256_bytes(
                            f"{image_digest or image_name}\0{ocr_text}".encode("utf-8")
                        ),
                    )
                )

    i = 0
    while i < len(lines):
        line = lines[i]
        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph(i)
            level = len(heading.group(1))
            title = heading.group(2).strip()
            section_stack = section_stack[: level - 1]
            section_stack.append(title)
            append_block(
                _block(
                    index=len(blocks),
                    document_id=document_id,
                    document_role=document_role,
                    section_path=section_stack,
                    content_type="heading",
                    raw_text=title,
                    start_line=i + 1,
                    end_line=i + 1,
                    parser_name=parser_name,
                    parser_version=parser_version,
                    source_basis=source_basis,
                )
            )
            i += 1
            continue

        if _is_table_start(lines, i):
            flush_paragraph(i)
            table_counter += 1
            table_id = f"table-{table_counter:04d}"
            table_start = i
            headers = _split_table_row(lines[i])
            i += 2  # header + separator
            rows: list[tuple[int, list[str]]] = []
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append((i, _split_table_row(lines[i])))
                i += 1
            table_end = (rows[-1][0] if rows else table_start + 1) + 1
            header_map = {str(column): value for column, value in enumerate(headers) if value}
            append_block(
                _block(
                    index=len(blocks),
                    document_id=document_id,
                    document_role=document_role,
                    section_path=section_stack,
                    content_type="table",
                    raw_text="\n".join(lines[table_start : table_end]),
                    start_line=table_start + 1,
                    end_line=table_end,
                    parser_name=parser_name,
                    parser_version=parser_version,
                    source_basis=source_basis,
                    table_id=table_id,
                    header_map=header_map,
                )
            )
            for row_index, (line_index, cells) in enumerate(rows):
                row_text = " | ".join(cells)
                append_block(
                    _block(
                        index=len(blocks),
                        document_id=document_id,
                        document_role=document_role,
                        section_path=section_stack,
                        content_type="table_row",
                        raw_text=row_text,
                        start_line=line_index + 1,
                        end_line=line_index + 1,
                        parser_name=parser_name,
                        parser_version=parser_version,
                        source_basis=source_basis,
                        table_id=table_id,
                        row_index=row_index,
                        header_map=header_map,
                    )
                )
            continue

        if not line.strip():
            flush_paragraph(i)
            i += 1
            continue

        if not paragraph_lines:
            paragraph_start = i + 1
        paragraph_lines.append(line)
        i += 1

    flush_paragraph(len(lines))
    return blocks


def _coverage(
    *,
    blocks: list[DuplicateEvidenceBlock],
    parsed_data: dict[str, Any],
    image_hash_warnings: list[str],
) -> CoverageSummary:
    text_blocks = [
        block
        for block in blocks
        if block.content_type in {"paragraph", "heading", "caption", "image_ocr"}
    ]
    table_blocks = [block for block in blocks if block.content_type == "table"]
    image_blocks = [block for block in blocks if block.content_type == "image"]
    pages_total = parsed_data.get("page_count")
    pages_parsed = parsed_data.get("parsed_page_count", pages_total)
    page_ratio = None
    if pages_total is not None:
        pages_total = max(0, int(pages_total))
        pages_parsed = max(0, int(pages_parsed or 0))
        page_ratio = min(1.0, pages_parsed / pages_total) if pages_total else 1.0

    warnings = [str(item) for item in parsed_data.get("warnings", []) if item]
    warnings.extend(image_hash_warnings)
    scanned_pages = int(parsed_data.get("scanned_page_count", 0) or 0)
    ocr_pages = int(parsed_data.get("ocr_page_count", 0) or 0)
    failed_ocr_pages = int(parsed_data.get("failed_ocr_page_count", 0) or 0)
    unresolved = int(parsed_data.get("unresolved_objects", 0) or 0) + failed_ocr_pages
    if parsed_data.get("coverage_status") not in (None, "complete", "partial", "insufficient"):
        warnings.append("coverage_status_invalid")
    if not blocks:
        status = "insufficient"
    elif (page_ratio is not None and page_ratio < 1) or unresolved or warnings:
        status = "partial"
    else:
        status = "complete"

    table_count = len(table_blocks)
    hashed_images = sum(bool(block.image_sha256) for block in image_blocks)
    text_count = len(text_blocks)
    return CoverageSummary(
        status=status,
        pages_total=pages_total,
        pages_parsed=pages_parsed,
        page_ratio=page_ratio,
        text_units=text_count,
        text_covered_units=text_count,
        text_ratio=1.0 if text_count else 0.0,
        table_count=table_count,
        structured_table_count=table_count,
        table_ratio=1.0 if table_count else 1.0,
        image_count=len(image_blocks),
        hashed_image_count=hashed_images,
        # OCR is intentionally not performed in S2-0.  It remains explicit so
        # S2-2A can add coverage without changing this manifest shape.
        ocr_image_count=sum(block.content_type == "image_ocr" for block in blocks),
        image_hash_ratio=(hashed_images / len(image_blocks)) if image_blocks else 1.0,
        image_ocr_ratio=(
            sum(block.content_type == "image_ocr" for block in blocks) / len(image_blocks)
            if image_blocks
            else 1.0
        ),
        scanned_page_count=scanned_pages,
        ocr_page_count=ocr_pages,
        failed_ocr_page_count=failed_ocr_pages,
        unresolved_objects=unresolved,
        warnings=warnings,
    )


def build_document_artifacts(
    *,
    document_id: str,
    document_role: str | None,
    original_filename: str,
    source_path: Path,
    markdown_path: Path,
    images_dir: Path | None,
    parsed_data: dict[str, Any],
) -> dict[str, Any]:
    """Create evidence JSON and a manifest next to the parsed Markdown file."""

    markdown = str(parsed_data.get("text") or "")
    parser_name = str(parsed_data.get("parser_name") or "markdown")
    parser_version = str(parsed_data.get("parser_version") or ADAPTER_VERSION)
    source_basis = str(parsed_data.get("source_basis") or "unknown")
    blocks = build_evidence_blocks(
        markdown,
        document_id=document_id,
        document_role=document_role,
        images_dir=images_dir,
        source_path=source_path,
        parser_name=parser_name,
        parser_version=parser_version,
        source_basis=source_basis,
        image_evidence=parsed_data.get("image_evidence"),
    )

    evidence_path = markdown_path.with_name(f"{markdown_path.stem}_evidence.json")
    manifest_path = markdown_path.with_name(f"{markdown_path.stem}_manifest.json")
    evidence_payload = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "document_id": document_id,
        "generated_at": _now().isoformat(),
        "blocks": [block.model_dump(mode="json") for block in blocks],
    }
    _atomic_write_json(evidence_path, evidence_payload)

    image_warnings: list[str] = []
    for block in blocks:
        if block.content_type == "image" and not block.image_sha256:
            image_warnings.append(f"image_hash_unavailable:{block.image_path or block.block_id}")

    coverage = _coverage(blocks=blocks, parsed_data=parsed_data, image_hash_warnings=image_warnings)
    source_info = ArtifactSource(
        name=original_filename,
        sha256=sha256_file(source_path),
        size_bytes=source_path.stat().st_size if source_path.is_file() else None,
        available=source_path.is_file(),
    )
    docling_path_value = parsed_data.get("docling_json_path")
    docling_path = Path(docling_path_value) if docling_path_value else None
    artifacts = {
        "markdown": _file_artifact(markdown_path),
        "evidence_blocks": _file_artifact(evidence_path),
        "docling_json": _file_artifact(docling_path),
        "images": _directory_artifact(images_dir),
    }
    warnings = list(coverage.warnings)
    if docling_path_value and not docling_path.is_file():
        warnings.append("docling_json_unavailable")
    if not source_info.available:
        warnings.append("source_unavailable")
    if not artifacts["markdown"].available:
        warnings.append("markdown_artifact_unavailable")
    if warnings != coverage.warnings:
        coverage = coverage.model_copy(
            update={"warnings": _unique(warnings), "status": "partial"}
        )
        warnings = list(coverage.warnings)

    counts = {
        "lines": len(markdown.splitlines()),
        "blocks": len(blocks),
        "paragraphs": sum(block.content_type == "paragraph" for block in blocks),
        "headings": sum(block.content_type == "heading" for block in blocks),
        "tables": sum(block.content_type == "table" for block in blocks),
        "table_rows": sum(block.content_type == "table_row" for block in blocks),
        "images": sum(block.content_type == "image" for block in blocks),
        "ocr_images": sum(block.content_type == "image_ocr" for block in blocks),
    }
    manifest = ArtifactManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        document_id=document_id,
        document_role=document_role,
        generated_at=_now(),
        source=source_info,
        artifacts=artifacts,
        parser_name=parser_name,
        parser_version=parser_version,
        evidence_block_count=len(blocks),
        counts=counts,
        coverage=coverage,
        warnings=warnings,
    )
    _atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
    return {
        "manifest_path": str(manifest_path),
        "evidence_blocks_path": str(evidence_path),
        "manifest": manifest,
        "coverage": coverage,
        "parser_name": parser_name,
        "parser_version": parser_version,
        "counts": counts,
    }


def load_artifact_manifest(path: str | Path | None) -> ArtifactManifest | None:
    """Load and validate a manifest; return ``None`` for missing/corrupt data."""

    if not path:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return ArtifactManifest.model_validate(payload)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Unable to load artifact manifest %s: %s", path, exc)
        return None


def load_evidence_blocks(
    path: str | Path | None,
    *,
    limit: int | None = None,
) -> list[DuplicateEvidenceBlock]:
    """Load deterministic blocks, optionally limiting the response size."""

    if not path:
        return []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_blocks = payload.get("blocks", []) if isinstance(payload, dict) else []
        if limit is not None:
            raw_blocks = raw_blocks[: max(0, limit)]
        return [DuplicateEvidenceBlock.model_validate(item) for item in raw_blocks]
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Unable to load evidence blocks %s: %s", path, exc)
        return []


def combine_coverage_summaries(
    summaries: Iterable[dict[str, Any] | None],
) -> tuple[str, list[str]]:
    """Combine document coverage for a task without treating missing data as complete."""

    statuses: list[str] = []
    warnings: list[str] = []
    for summary in summaries:
        if summary is None:
            summary = {}
        elif not isinstance(summary, dict):
            statuses.append("insufficient")
            warning = "coverage_summary_invalid"
            if warning not in warnings:
                warnings.append(warning)
            continue
        status = str(summary.get("status") or "insufficient")
        if status not in {"complete", "partial", "insufficient"}:
            warning = "coverage_status_invalid"
            if warning not in warnings:
                warnings.append(warning)
            status = "partial"
        statuses.append(status)
        for warning in summary.get("warnings", []) or []:
            warning = str(warning)
            if warning and warning not in warnings:
                warnings.append(warning)
    if not statuses or "insufficient" in statuses:
        return "insufficient", warnings
    if "partial" in statuses:
        return "partial", warnings
    return "complete", warnings


__all__ = [
    "ADAPTER_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "build_document_artifacts",
    "build_evidence_blocks",
    "combine_coverage_summaries",
    "load_artifact_manifest",
    "load_evidence_blocks",
    "normalize_text",
    "sha256_file",
]
