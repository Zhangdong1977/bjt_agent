"""Document parsing tasks."""

import asyncio
import base64
import json
import logging
import re
import redis
from pathlib import Path

from typing import Literal

from billiard.exceptions import SoftTimeLimitExceeded

from backend.celery_app import celery_app
from backend.models import Document

logger = logging.getLogger(__name__)

_SOURCE_BASIS_BY_DOC_TYPE = {
    "tender": "tender",
    "duplicate_tender": "tender",
    "duplicate_public_reference": "public",
    "bid": "bidder_authored",
    "duplicate_left": "bidder_authored",
    "duplicate_right": "bidder_authored",
}


def _natural_sort_key(text: str) -> list:
    """Sort key for natural string ordering with embedded numbers.

    Example: 'image_10' should sort after 'image_2' not before 'image_1'.
    Returns a list of (is_digit, value) tuples for proper numeric sorting.
    """
    import re
    parts = re.split(r'(\d+)', text)
    return [(int(p) if p.isdigit() else p.lower(), i) for i, p in enumerate(parts)]


# Redis connection pool for _publish_parse_progress - avoids creating new connections each call
# which was causing thread exhaustion and timeouts under heavy load
_redis_connection_pool = None

def _get_redis_pool():
    """Get or create the shared Redis connection pool."""
    global _redis_connection_pool
    if _redis_connection_pool is None:
        from backend.config import get_settings
        settings = get_settings()
        # Use a pool with limited connections to prevent resource exhaustion
        _redis_connection_pool = redis.ConnectionPool.from_url(
            settings.redis_url,
            max_connections=10,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            socket_keepalive=True,
        )
    return _redis_connection_pool

def _publish_parse_progress(
    document_id: str,
    stage: str,
    processed: int,
    total: int,
    eta_seconds: int,
    sub_stage: str | None = None,
    stage_counts: dict[str, int] | None = None,
) -> None:
    """Publish a parse progress event to Redis Stream.

    Args:
        document_id: The document UUID
        stage: Stage name (e.g., "extracting_text", "saving", "parsing_pdf")
        processed: Number of elements processed so far
        total: Total number of elements to process
        eta_seconds: Estimated seconds remaining
        sub_stage: Optional Docling internal stage (e.g., "layout", "table")
        stage_counts: Optional per-stage real page counts
            {"preprocess": 42, "layout": 30, "table": 28, "assemble": 25}
    """
    import redis

    try:
        pool = _get_redis_pool()
        r = redis.Redis(connection_pool=pool)
        stream_key = f"sse:stream:doc_parse:{document_id}"
        event = {
            "type": "parse_progress",
            "stage": stage,
            "processed": processed,
            "total": total,
            "eta_seconds": eta_seconds,
        }
        if sub_stage is not None:
            event["sub_stage"] = sub_stage
        if stage_counts is not None:
            event["stage_counts"] = stage_counts
        r.xadd(stream_key, {"data": json.dumps(event)})
    except Exception as e:
        logger.warning(f"[PROGRESS] Failed to publish parse progress for {document_id}: {e}")


def _clean_sd_abs_pos_elements(html_content: str) -> str:
    """Remove sd-abs-pos elements from HTML content.

    These elements are LibreOffice conversion artifacts with absolute positioning
    that cause layout issues in web view (document height expansion to 100k+ pixels).

    Args:
        html_content: HTML content as string

    Returns:
        Cleaned HTML content with sd-abs-pos elements removed
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")

    # Find and remove all elements with sd-abs-pos class
    removed_count = 0
    for element in soup.find_all(class_="sd-abs-pos"):
        element.decompose()
        removed_count += 1

    if removed_count > 0:
        logger.info(f"Removed {removed_count} sd-abs-pos elements from HTML")

    return str(soup)


def _fix_html_image_paths(html_content: str, images_dir_name: str) -> str:
    """Fix image paths in HTML to point to the correct images directory.

    LibreOffice generates HTML with relative image paths, but images are saved
    to a separate _images/ directory. This function updates the paths.

    Args:
        html_content: HTML content with <img> tags
        images_dir_name: Name of the images directory (e.g., "xxx_images")

    Returns:
        HTML with corrected image paths
    """
    def replace_img_src(match):
        img_tag = match.group(0)
        src_match = re.search(r'src=["\']([^"\']+)["\']', img_tag)
        if not src_match:
            return img_tag
        src = src_match.group(1)
        # Skip if already absolute path or already points to images dir
        if src.startswith(('http://', 'https://', '/')) or images_dir_name in src:
            return img_tag
        # Prepend images directory to relative path
        new_src = f"{images_dir_name}/{src}"
        return img_tag.replace(f'"{src}"', f'"{new_src}"').replace(f"'{src}'", f"'{new_src}'")

    return re.sub(r'<img[^>]+>', replace_img_src, html_content)


def _insert_missing_img_tags(html_content: str, images_dir: Path) -> str:
    """Insert <img> tags for images that exist in the directory but are not referenced in HTML.

    LibreOffice sometimes extracts images to a _images directory without referencing
    them in the HTML. This function finds unreferenced images and inserts img tags.

    Args:
        html_content: HTML content that may be missing img tags
        images_dir: Path to the directory containing images

    Returns:
        HTML with img tags inserted for unreferenced images
    """
    if not images_dir.exists():
        return html_content

    # Find all image files in the directory
    image_extensions = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
        ".tif",
        ".tiff",
        ".jp2",
        ".jpx",
        ".j2k",
        ".j2c",
        ".jpc",
    }
    image_files = []
    for ext in image_extensions:
        image_files.extend(images_dir.glob(f"*{ext}"))

    if not image_files:
        return html_content

    # Build a set of image filenames already referenced in HTML
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")
    referenced_images = set()
    for img in soup.find_all("img"):
        src = img.get("src", "")
        # Extract just the filename from the path
        referenced_images.add(Path(src).name)

    # Find unreferenced images
    unreferenced_images = [img for img in image_files if img.name not in referenced_images]

    if not unreferenced_images:
        return html_content

    # Insert img tags at the end of the body (or before closing body tag)
    # Sort images by name for consistent ordering
    unreferenced_images.sort(key=lambda x: x.name)

    # Find insertion point - end of body or end of content
    body = soup.find("body")
    if body is None:
        # If no body, append to end of document
        insert_point = soup
    else:
        insert_point = body

    # Create img tags for unreferenced images
    for img_path in unreferenced_images:
        img_tag = soup.new_tag("img")
        img_tag["src"] = f"{images_dir.name}/{img_path.name}"
        img_tag["alt"] = img_path.stem  # Use filename without extension as alt text
        img_tag["style"] = "max-width: 100%; height: auto; display: block; margin: 1em 0;"
        insert_point.append(img_tag)
        logger.info(f"Inserted missing img tag for unreferenced image: {img_path.name}")

    return str(soup)


def _embed_image_descriptions_in_md(
    markdown: str,
    descriptions: dict[str, str] | None,
) -> str:
    """Insert deterministic image descriptions below matching Markdown links.

    This compatibility helper deliberately performs no image understanding or
    network work.  The parser and the S2 image-evidence service may populate a
    filename-to-description map, while callers that do not have descriptions
    receive the original Markdown byte-for-byte.
    """

    if not markdown or not descriptions:
        return markdown

    normalized: dict[str, str] = {}
    for key, value in descriptions.items():
        if value is None:
            continue
        filename = str(key).replace("\\", "/").split("/")[-1]
        filename = filename.split("?", 1)[0].split("#", 1)[0]
        if filename and str(value):
            normalized[filename] = str(value)

    if not normalized:
        return markdown

    image_pattern = re.compile(r"^([ \t]*!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)[ \t]*)$")
    output: list[str] = []
    for line in markdown.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        match = image_pattern.match(body)
        if not match:
            output.append(line)
            continue
        image_path = match.group(2).replace("\\", "/")
        image_name = image_path.rsplit("/", 1)[-1]
        image_name = image_name.split("?", 1)[0].split("#", 1)[0]
        description = normalized.get(image_name)
        if not description:
            output.append(line)
            continue
        output.append(line)
        # Keep the source newline style.  If the image is the final line,
        # introduce a separator before the description because the source line
        # itself has no terminator.
        description_line = f"图片内容: {description}"
        if newline:
            output.append(description_line + newline)
        else:
            output.append("\n" + description_line)
    return "".join(output)


async def _process_images_with_llm(
    images: list[dict],
    api_key: str,
    api_base: str,
    model: str,
    document_id: str | None = None,
) -> list[str]:
    """Legacy MiniMax-compatible image description helper.

    The active S2 parser uses :class:`SelectiveImageEvidenceService` instead;
    this bounded adapter remains available for older integrations and tests
    that call the historical helper directly.  It never runs unless a caller
    explicitly invokes it.
    """

    import httpx

    descriptions: list[str] = []
    for image in (images or [])[:5]:
        filename = str(image.get("filename") or "image")
        try:
            payload = base64.b64encode(image.get("data") or b"").decode("ascii")
            extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
            mime = (
                f"image/{extension}"
                if extension in {"png", "jpeg", "jpg", "gif", "webp"}
                else "image/png"
            )
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{api_base.rstrip('/')}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "Describe this image in detail. Focus on text, "
                                            "diagrams, tables, and important visual elements."
                                        ),
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:{mime};base64,{payload}"
                                        },
                                    },
                                ],
                            }
                        ],
                        "max_tokens": 500,
                    },
                )
            if response.status_code != 200:
                logger.warning(
                    "Image understanding API error for %s: %s",
                    filename,
                    response.status_code,
                )
                continue
            try:
                data = response.json()
            except Exception as exc:
                logger.warning("Invalid image understanding JSON for %s: %s", filename, exc)
                continue
            choices = data.get("choices") if isinstance(data, dict) else None
            if not choices:
                continue
            content = (((choices[0] or {}).get("message") or {}).get("content"))
            if content is None:
                continue
            # MiniMax/DeepSeek responses occasionally include hidden reasoning
            # tags.  Do not persist those tokens into document evidence.
            content = re.sub(
                r"<(?:think|thought)>.*?</(?:think|thought)>",
                "",
                str(content),
                flags=re.IGNORECASE | re.DOTALL,
            )
            descriptions.append(f"[Image: {filename}] {content.strip()}")
        except Exception as exc:
            logger.warning("Failed to process image %s: %s", filename, exc)
            descriptions.append(f"[Image: {filename}] (Image processing failed)")
    return descriptions


async def _save_parsed_content(file_path: Path, parsed_data: dict, document: Document, settings, document_id: str) -> dict:
    """Save parsed content to disk and update document record."""
    _publish_parse_progress(document_id, "saving", 1, 3, 0)

    parsed_dir = file_path.parent
    md_path = parsed_dir / f"{file_path.stem}_parsed.md"
    images_dir = parsed_dir / f"{file_path.stem}_images"

    md_content = parsed_data["text"]
    md_path.write_text(md_content, encoding="utf-8")

    # Images are already on disk (written by DirectFileImageHandler during parsing)
    document.parsed_images_dir = str(images_dir) if parsed_data["images"] else None

    document.parsed_markdown_path = str(md_path)
    # Docling writes its structured JSON beside the source PDF.  The parser
    # result already contains the path; persist it on the Document in the same
    # transaction as the Markdown output so downstream duplicate checks never
    # have to guess where the structured artifact lives.
    docling_json_path = parsed_data.get("docling_json_path")
    document.docling_json_path = str(docling_json_path) if docling_json_path else None
    document.word_count = len(md_content.split())
    document.page_count = parsed_data.get("page_count")
    document.artifact_manifest_path = None
    document.evidence_blocks_path = None
    if not parsed_data.get("source_basis"):
        parsed_data["source_basis"] = _SOURCE_BASIS_BY_DOC_TYPE.get(
            document.doc_type, "unknown"
        )
    if document.doc_type in {"duplicate_tender", "duplicate_public_reference"}:
        from backend.services.document_artifacts import sha256_file

        snapshot_hash = sha256_file(file_path)
        document.source_snapshot_path = str(file_path)
        document.source_snapshot_hash = snapshot_hash
        if not document.source_version:
            document.source_version = (
                f"upload-{snapshot_hash[:12]}" if snapshot_hash else "upload-unhashed"
            )
        metadata = dict(document.source_metadata or {})
        metadata.update(
            {
                "snapshot_kind": "uploaded_file",
                "original_filename": document.original_filename,
                "source_basis": parsed_data["source_basis"],
            }
        )
        document.source_metadata = metadata

    # S2-0 deterministic evidence IR and artifact manifest.  Artifact
    # generation is deliberately best-effort: a parser result must remain
    # usable for the existing review flow even if a secondary JSON artifact
    # cannot be written.  The coverage summary records that degradation.
    try:
        from backend.services.document_artifacts import build_document_artifacts

        artifact_result = build_document_artifacts(
            document_id=document.id,
            document_role=document.doc_type,
            original_filename=document.original_filename,
            source_path=file_path,
            markdown_path=md_path,
            images_dir=images_dir if parsed_data.get("images") else None,
            parsed_data=parsed_data,
        )
        document.artifact_manifest_path = artifact_result["manifest_path"]
        document.evidence_blocks_path = artifact_result["evidence_blocks_path"]
        document.parser_name = artifact_result["parser_name"]
        document.parser_version = artifact_result["parser_version"]
        document.coverage_summary = artifact_result["coverage"].model_dump(mode="json")
    except Exception as exc:
        logger.warning(
            "[PARSE] Failed to generate S2-0 artifacts for %s: %s",
            document_id,
            exc,
            exc_info=True,
        )
        document.parser_name = str(parsed_data.get("parser_name") or "unknown")
        document.parser_version = str(parsed_data.get("parser_version") or "unknown")
        document.coverage_summary = {
            "status": "partial",
            "pages_total": document.page_count,
            "pages_parsed": document.page_count,
            "page_ratio": 1.0 if document.page_count else None,
            "text_units": 0,
            "text_covered_units": 0,
            "text_ratio": 0.0,
            "table_count": 0,
            "structured_table_count": 0,
            "table_ratio": 1.0,
            "image_count": len(parsed_data.get("images") or []),
            "hashed_image_count": 0,
            "ocr_image_count": 0,
            "image_hash_ratio": 0.0,
            "image_ocr_ratio": 0.0,
            "scanned_page_count": int(parsed_data.get("scanned_page_count", 0) or 0),
            "ocr_page_count": int(parsed_data.get("ocr_page_count", 0) or 0),
            "failed_ocr_page_count": int(parsed_data.get("failed_ocr_page_count", 0) or 0),
            "unresolved_objects": 1,
            "warnings": [f"artifact_generation_failed:{type(exc).__name__}"],
        }

    document.status = "parsed"

    _publish_parse_progress(document_id, "saving", 3, 3, 0)

    return {
        "status": "success",
        "document_id": document.id,
        "parsed_markdown_path": str(md_path),
        "docling_json_path": document.docling_json_path,
        "artifact_manifest_path": document.artifact_manifest_path,
        "evidence_blocks_path": document.evidence_blocks_path,
        "coverage_status": (document.coverage_summary or {}).get("status"),
        "page_count": document.page_count,
        "word_count": document.word_count,
    }


async def _augment_duplicate_image_evidence(
    file_path: Path,
    parsed_data: dict,
    document: Document,
    settings,
) -> dict:
    """Hash all duplicate-check images and selectively OCR useful/scan images."""

    if not str(document.doc_type).startswith("duplicate_"):
        return parsed_data

    from backend.services.duplicate_image_evidence import (
        SelectiveImageEvidenceService,
        classify_pdf_pages,
        render_pdf_pages,
    )

    images_dir = file_path.parent / f"{file_path.stem}_images"
    service = SelectiveImageEvidenceService(
        cache_dir=settings.workspace_path / ".duplicate_cache" / "image_evidence",
        ocr_enabled=settings.duplicate_ocr_enabled,
        remote_ocr_enabled=settings.duplicate_remote_ocr_enabled,
        vision_enabled=settings.duplicate_vision_enabled,
        max_ocr_images=settings.duplicate_ocr_max_images,
        max_remote_calls=settings.duplicate_remote_ocr_max_calls,
        max_vision_calls=settings.duplicate_vision_max_calls,
        min_local_confidence=settings.duplicate_ocr_min_local_confidence,
        normalization_cache_dir=settings.workspace_path / ".ocr_image_cache",
    )
    evidence_by_name: dict[str, dict] = {}
    scan_paths: dict[int, Path] = {}
    classifications = []

    if file_path.suffix.lower() == ".pdf":
        classifications = await asyncio.to_thread(
            classify_pdf_pages,
            file_path,
            text_threshold=settings.duplicate_scan_text_threshold,
        )
        scan_pages = [page.page_number for page in classifications if page.kind == "scan"]
        if scan_pages:
            scan_paths = await asyncio.to_thread(
                render_pdf_pages,
                file_path,
                scan_pages,
                images_dir,
            )
            existing_names = {
                str(item.get("filename")) for item in parsed_data.get("images", [])
            }
            markdown_refs: list[str] = []
            for page_number, path in scan_paths.items():
                if path.name not in existing_names:
                    parsed_data.setdefault("images", []).append(
                        {"filename": path.name, "data": b""}
                    )
                markdown_refs.append(
                    f"![扫描页 {page_number}]({images_dir.name}/{path.name})"
                )
            if markdown_refs:
                parsed_data["text"] = (
                    str(parsed_data.get("text") or "").rstrip()
                    + "\n\n"
                    + "\n\n".join(markdown_refs)
                    + "\n"
                )

    image_names = [
        str(item.get("filename"))
        for item in parsed_data.get("images", [])
        if item.get("filename")
    ]
    scan_name_to_page = {path.name: page for page, path in scan_paths.items()}
    warnings: list[str] = list(parsed_data.get("warnings") or [])
    ocr_scan_pages = 0
    failed_scan_pages = 0
    for name in dict.fromkeys(image_names):
        path = images_dir / Path(name).name
        if not path.is_file():
            warnings.append(f"image_unavailable:{Path(name).name}")
            continue
        page_number = scan_name_to_page.get(path.name)
        evidence = await service.analyze(
            path,
            force_ocr=page_number is not None,
            page_number=page_number,
        )
        evidence_by_name[path.name] = evidence.to_dict()
        warnings.extend(evidence.warnings)
        if page_number is not None:
            if evidence.ocr_text:
                ocr_scan_pages += 1
            else:
                failed_scan_pages += 1

    scanned_page_count = len(scan_paths)
    text_page_count = sum(page.kind == "text" for page in classifications)
    if classifications:
        parsed_data["page_classification"] = [
            {
                "page_number": page.page_number,
                "kind": page.kind,
                "extracted_text_length": page.extracted_text_length,
                "image_count": page.image_count,
            }
            for page in classifications
        ]
        parsed_data["parsed_page_count"] = text_page_count + ocr_scan_pages
    parsed_data["scanned_page_count"] = scanned_page_count
    parsed_data["ocr_page_count"] = ocr_scan_pages
    parsed_data["failed_ocr_page_count"] = failed_scan_pages
    parsed_data["unresolved_objects"] = int(parsed_data.get("unresolved_objects", 0) or 0)
    parsed_data["image_evidence"] = evidence_by_name
    parsed_data["warnings"] = list(dict.fromkeys(warnings))
    return parsed_data


async def _parse_document_internal(document: Document, file_path: Path, settings) -> dict:
    """Internal document parsing logic for DOCX/PDF files."""
    import time as time_module

    suffix = file_path.suffix.lower()
    start_time = time_module.time()
    file_size_mb = file_path.stat().st_size / (1024 * 1024)

    logger.info(
        f"[PARSE] Starting: document_id={document.id}, file={file_path.name}, "
        f"type={suffix}, size={file_size_mb:.2f}MB"
    )

    if suffix == ".pdf":
        parsed_data = await _parse_pdf_with_markitdown(file_path, document_id=document.id)
        elapsed = time_module.time() - start_time
        logger.info(
            f"[PARSE] PDF done: document_id={document.id}, elapsed={elapsed:.1f}s, "
            f"md_length={len(parsed_data.get('text', ''))}, "
            f"images={len(parsed_data.get('images', []))}, "
            f"pages={parsed_data.get('page_count')}"
        )
    elif suffix == ".xlsx":
        parsed_data = await _parse_xlsx(file_path, settings=settings, document_id=document.id)
        elapsed = time_module.time() - start_time
        logger.info(
            f"[PARSE] XLSX done: document_id={document.id}, elapsed={elapsed:.1f}s, "
            f"md_length={len(parsed_data.get('text', ''))}, "
            f"images={len(parsed_data.get('images', []))}"
        )
    else:
        # DOCX/DOC parsing with mammoth progress callback
        last_published_time = 0
        MIN_PUBLISH_INTERVAL = 0.5

        def docx_progress_callback(processed: int, total: int):
            nonlocal last_published_time
            logger.info(f"[PARSE] DOCX progress callback: processed={processed}, total={total}")

            if total <= 0:
                return

            current_time = time_module.time()
            if processed < total and current_time - last_published_time < MIN_PUBLISH_INTERVAL:
                return

            last_published_time = current_time
            _publish_parse_progress(document.id, "extracting_text", processed, total, 0)

        parsed_data = await _parse_docx(file_path, progress_callback=docx_progress_callback, document_id=document.id)
        elapsed = time_module.time() - start_time
        logger.info(
            f"[PARSE] DOCX done: document_id={document.id}, elapsed={elapsed:.1f}s, "
            f"md_length={len(parsed_data.get('text', ''))}"
        )

    parsed_data = await _augment_duplicate_image_evidence(
        file_path, parsed_data, document, settings
    )

    return await _save_parsed_content(file_path, parsed_data, document, settings, document.id)


@celery_app.task(bind=True, name="backend.tasks.document_parser.parse_document")
def parse_document(self, document_id: str) -> dict:
    """Parse a document (PDF or Word) and extract text and images.

    This is a Celery task that runs asynchronously.
    After text extraction, it optionally processes images with LLM understanding.
    """
    import time as time_module
    from backend.config import get_settings
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    task_start = time_module.time()
    logger.info(f"[PARSE] ====== Task received: document_id={document_id} ======")

    settings = get_settings()

    # Create a fresh engine and session factory for this task to avoid event loop issues
    # honor DB_USE_PGBOUNCER: transaction mode doesn't support prepared statements
    _task_connect_args = {"timeout": 30, "command_timeout": 120}
    if settings.db_use_pgbouncer:
        _task_connect_args["statement_cache_size"] = 0
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=5,
        pool_recycle=1800,
        connect_args=_task_connect_args,
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async def _parse():
        async with session_factory() as db:
            from sqlalchemy import select

            result = await db.execute(select(Document).where(Document.id == document_id))
            document = result.scalar_one_or_none()

            if not document:
                logger.error(f"[PARSE] Document not found in DB: {document_id}")
                return {"status": "error", "message": "文档不存在或已被删除"}

            usage_token = None
            try:
                from backend.models import Project, User
                from backend.services.usage_context import UsageContext, set_usage_context

                owner_user_id = document.owner_user_id
                if owner_user_id is None and document.project_id:
                    project = (
                        await db.execute(
                            select(Project).where(Project.id == document.project_id)
                        )
                    ).scalar_one_or_none()
                    owner_user_id = project.user_id if project else None
                user = None
                if owner_user_id:
                    user = (
                        await db.execute(select(User).where(User.id == owner_user_id))
                    ).scalar_one_or_none()
                if owner_user_id:
                    usage_token = set_usage_context(
                        UsageContext(
                            external_user_id=(user.external_user_id if user else None),
                            local_user_id=str(owner_user_id),
                            user_name=(user.username if user else str(owner_user_id))
                            or str(owner_user_id),
                            enterprise_name=(user.enterprise_name if user else None),
                            interior_user=bool(user.interior_user) if user else False,
                            project_id=document.project_id,
                            task_id=None,
                            todo_id=None,
                        )
                    )
            except Exception:
                usage_token = None
                logger.warning("[PARSE] OCR usage context unavailable", exc_info=True)

            document.status = "parsing"
            await db.flush()

            file_path = Path(document.file_path)
            # NFS 跨节点读竞态兜底：上传侧已 fsync，但 client→server 传播仍可能有
            # 亚秒级窗口。这里短重试吸收掉，避免误判「文件不存在」。
            for _attempt in range(4):
                if file_path.exists():
                    break
                logger.warning(
                    f"[PARSE] File not visible yet (attempt {_attempt + 1}/4): {file_path}"
                )
                if _attempt < 3:
                    time_module.sleep(0.5)
            else:
                document.status = "failed"
                document.parse_error = "文件不存在，请重新上传"
                await db.flush()
                await db.commit()
                logger.error(f"[PARSE] File not found on disk: {file_path}")
                _publish_parse_progress(document.id, "failed", 0, 0, 0, sub_stage="error")
                return {"status": "error", "message": "文件不存在，请重新上传"}

            try:
                result = await _parse_document_internal(document, file_path, settings)
                elapsed = time_module.time() - task_start
                logger.info(
                    f"[PARSE] ====== Task completed: document_id={document_id}, "
                    f"elapsed={elapsed:.1f}s, pages={result.get('page_count')}, "
                    f"word_count={result.get('word_count')} ======"
                )
                await db.commit()
                # Publish completion event after successful save and commit
                _publish_parse_progress(document.id, "completed", 1, 1, 0)
                return result
            except Exception as e:
                elapsed = time_module.time() - task_start
                logger.error(
                    f"[PARSE] ====== Task FAILED: document_id={document_id}, "
                    f"elapsed={elapsed:.1f}s, error_type={type(e).__name__}, "
                    f"error={e} ======",
                    exc_info=True,
                )
                document.status = "failed"
                document.parse_error = str(e)
                await db.flush()
                await db.commit()
                _publish_parse_progress(document.id, "failed", 0, 0, 0, sub_stage="error")
                return {"status": "error", "message": str(e)}
            finally:
                if usage_token is not None:
                    try:
                        from backend.services.usage_context import reset_usage_context

                        reset_usage_context(usage_token)
                    except Exception:
                        pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_parse())
    except SoftTimeLimitExceeded:
        elapsed = time_module.time() - task_start
        logger.error(
            f"[PARSE] ====== Task TIMEOUT (SoftTimeLimitExceeded): document_id={document_id}, "
            f"elapsed={elapsed:.1f}s ======"
        )
        error_msg = "文档解析超时，文件过大或内容过于复杂。建议上传较小的文件或拆分后重新上传。"
        _publish_parse_progress(document_id, "failed", 0, 0, 0, sub_stage="timeout")
        _mark_document_failed(document_id, error_msg, session_factory)
        return {"status": "error", "message": error_msg}
    finally:
        try:
            loop.run_until_complete(engine.dispose())
        except Exception:
            pass
        loop.close()


async def _parse_pdf_with_docling(file_path: Path, document_id: str = "") -> dict:
    """Parse PDF file using Docling converter with multi-stage weighted progress.

    Produces Markdown with image file links + DoclingDocument JSON.
    OCR is disabled during parsing (deferred to review-time tools).

    Progress is reported as weighted page-equivalents across 4 pipeline stages
    (preprocess, layout, table, assemble), each contributing 25% weight.
    This provides continuous feedback from the first page entering the pipeline,
    instead of waiting for pages to clear all stages.

    Args:
        file_path: Path to the PDF file
        document_id: Document UUID for progress reporting

    Returns:
        Dict with text (Markdown), images, and page_count
    """
    import threading
    import time as time_module
    from backend.parsers.docling_converter import DoclingConverter, ProgressReportingPdfPipeline

    file_size = file_path.stat().st_size
    logger.info(f"[DOCLING] Starting: {file_path.name} ({file_size / (1024*1024):.2f}MB)")

    # Get total page count instantly via PyMuPDF for progress reporting
    total_pages = 0
    try:
        import fitz
        doc = fitz.open(str(file_path))
        total_pages = len(doc)
        doc.close()
        logger.info(f"[DOCLING] PDF total pages (PyMuPDF): {total_pages}")
    except Exception as e:
        logger.warning(f"[DOCLING] Failed to get PDF page count via PyMuPDF: {e}")

    # Per-stage real page tracking (4 active stages, ocr skipped)
    STAGE_NAMES = ["preprocess", "layout", "table", "assemble"]

    # Report initial progress: loading models
    _publish_parse_progress(document_id, "parsing_pdf", 0, total_pages, 0, sub_stage="loading_models")

    # Set up per-stage progress callback using sets for accurate counting
    last_published_time = 0.0
    stage_pages: dict[str, set[int]] = {}
    progress_lock = threading.Lock()
    MIN_PUBLISH_INTERVAL = 0.5
    callback_invoke_count = 0

    def on_stage_progress(stage_name: str, page_no: int):
        nonlocal last_published_time, callback_invoke_count
        callback_invoke_count += 1
        with progress_lock:
            stage_pages.setdefault(stage_name, set()).add(page_no)

            # Log first call and every 100th call
            if callback_invoke_count == 1:
                logger.info(
                    f"[DOCLING] First page finished stage: stage={stage_name}, page={page_no}, "
                    f"doc_id={document_id}"
                )
            elif callback_invoke_count % 100 == 0:
                counts_debug = {name: len(stage_pages.get(name, set())) for name in STAGE_NAMES}
                logger.info(
                    f"[DOCLING] Progress: calls={callback_invoke_count}, "
                    f"stages={counts_debug}, doc_id={document_id}"
                )

            current_time = time_module.time()
            if current_time - last_published_time < MIN_PUBLISH_INTERVAL:
                return
            last_published_time = current_time

            counts = {name: len(stage_pages.get(name, set())) for name in STAGE_NAMES}
            effective_count = min(counts.values()) if counts else 0

            _publish_parse_progress(
                document_id, "parsing_pdf",
                effective_count, total_pages, 0,
                stage_counts=counts,
            )

    ProgressReportingPdfPipeline.set_callback(on_stage_progress)

    images_dir = file_path.parent / f"{file_path.stem}_images"
    docling_json_path = file_path.parent / f"{file_path.stem}_docling.json"

    convert_start = time_module.time()
    converter = DoclingConverter()
    logger.info(f"[DOCLING] Calling converter.convert()...")
    result = await asyncio.to_thread(
        converter.convert, file_path, images_dir=images_dir, docling_json_path=docling_json_path
    )
    convert_elapsed = time_module.time() - convert_start

    # Clear callback after conversion
    ProgressReportingPdfPipeline.set_callback(None)

    # Publish final parsing completion with all stages at total
    final_counts = {name: total_pages for name in STAGE_NAMES}
    _publish_parse_progress(
        document_id, "parsing_pdf", total_pages, total_pages, 0,
        stage_counts=final_counts,
    )

    # Log coverage: Docling parsed pages vs PDF total pages
    parsed_pages = result.page_count or 0
    coverage_pct = (parsed_pages / total_pages * 100) if total_pages > 0 else 0
    logger.info(
        f"[DOCLING] Conversion done: elapsed={convert_elapsed:.1f}s, "
        f"md={len(result.markdown_content)} chars, "
        f"{len(result.images)} images, "
        f"parsed_pages={parsed_pages}/{total_pages} ({coverage_pct:.1f}%)"
    )

    # Warn if significant content was missed
    if total_pages > 0 and parsed_pages < total_pages:
        logger.warning(
            f"[DOCLING] INCOMPLETE: Docling only parsed {parsed_pages}/{total_pages} pages "
            f"({coverage_pct:.1f}%). Possible causes: document_timeout, memory, or pipeline error."
        )

    return {
        "text": result.markdown_content,
        "images": [{"filename": img.filename, "data": img.data} for img in result.images],
        "page_count": result.page_count,
        "docling_json_path": str(docling_json_path),
        "parser_name": "docling",
        "parser_version": "docling-table-structure",
        "parsed_page_count": parsed_pages,
    }


async def _parse_pdf_with_markitdown(file_path: Path, document_id: str = "") -> dict:
    """Parse PDF file using Markitdown converter with per-page progress reporting.

    Uses PyMuPDF (fitz) for page-by-page text and image extraction.
    Progress is reported after each page is processed.

    Args:
        file_path: Path to the PDF file
        document_id: Document UUID for progress reporting

    Returns:
        Dict with text (Markdown), images, and page_count
    """
    import time as time_module
    from backend.parsers.markitdown_converter import MarkitdownConverter

    file_size = file_path.stat().st_size
    logger.info(f"[MARKITDOWN] Starting PDF: {file_path.name} ({file_size / (1024*1024):.2f}MB)")

    images_dir = file_path.parent / f"{file_path.stem}_images"

    last_published_time = 0.0
    MIN_PUBLISH_INTERVAL = 0.5

    def on_page_progress(processed: int, total: int):
        nonlocal last_published_time
        current_time = time_module.time()
        if processed < total and current_time - last_published_time < MIN_PUBLISH_INTERVAL:
            return
        last_published_time = current_time
        _publish_parse_progress(document_id, "parsing_pdf", processed, total, 0)

    convert_start = time_module.time()
    converter = MarkitdownConverter()
    result = await asyncio.to_thread(
        converter.convert, file_path, images_dir=images_dir, progress_callback=on_page_progress
    )
    convert_elapsed = time_module.time() - convert_start

    _publish_parse_progress(document_id, "parsing_pdf", result.page_count or 0, result.page_count or 0, 0)

    logger.info(
        f"[MARKITDOWN] Conversion done: elapsed={convert_elapsed:.1f}s, "
        f"md={len(result.markdown_content)} chars, "
        f"{len(result.images)} images, pages={result.page_count}"
    )

    return {
        "text": result.markdown_content,
        "images": [{"filename": img.filename, "data": img.data} for img in result.images],
        "page_count": result.page_count,
        "parser_name": "markitdown",
        "parser_version": "pymupdf-markdown",
        "parsed_page_count": result.page_count,
    }


async def _parse_xlsx(file_path: Path, settings, document_id: str = "") -> dict:
    """Parse XLSX file using openpyxl: each visible sheet becomes a Markdown table.

    Progress is reported per worksheet (stage ``extracting_text``) so the parse
    card stays alive for multi-sheet workbooks.  page_count stays None — a
    spreadsheet has no page concept (same contract as DOCX).
    """
    import time as time_module
    from backend.parsers.markitdown_converter import MarkitdownConverter

    file_size = file_path.stat().st_size
    logger.info(f"[XLSX] Starting: {file_path.name} ({file_size / (1024 * 1024):.2f}MB)")

    images_dir = file_path.parent / f"{file_path.stem}_images"

    def on_sheet_progress(processed: int, total: int):
        _publish_parse_progress(document_id, "extracting_text", processed, max(total, 1), 0)

    convert_start = time_module.time()
    converter = MarkitdownConverter(
        xlsx_max_rows=settings.xlsx_max_rows_per_sheet,
        xlsx_max_cols=settings.xlsx_max_cols_per_sheet,
    )
    result = await asyncio.to_thread(
        converter.convert, file_path, images_dir=images_dir, progress_callback=on_sheet_progress
    )
    convert_elapsed = time_module.time() - convert_start

    logger.info(
        f"[XLSX] Conversion done: elapsed={convert_elapsed:.1f}s, "
        f"md={len(result.markdown_content)} chars, images={len(result.images)}"
    )

    return {
        "text": result.markdown_content,
        "images": [{"filename": img.filename, "data": img.data} for img in result.images],
        "page_count": None,
        "parser_name": "markitdown",
        "parser_version": "openpyxl-tables/v1",
    }


async def _parse_docx(file_path: Path, progress_callback=None, document_id: str = "") -> dict:
    """Parse DOCX file using markitdown.

    Images are written directly to disk by DirectFileImageHandler during conversion,
    so the returned markdown already contains file-path references instead of base64 data URIs.

    Args:
        file_path: Path to the DOCX file
        progress_callback: Optional callback for progress updates (processed, total)
        document_id: Document UUID for progress reporting

    Returns:
        Dict with text (Markdown), images, and page_count (None)
    """
    from backend.parsers.markitdown_converter import MarkitdownConverter

    file_size = file_path.stat().st_size
    logger.info(f"Markitdown parsing: {file_path} ({file_size / (1024*1024):.2f}MB)")

    images_dir = file_path.parent / f"{file_path.stem}_images"

    converter = MarkitdownConverter()
    result = converter.convert(file_path, progress_callback=progress_callback, images_dir=images_dir)

    logger.info(f"Markitdown conversion successful: {len(result.markdown_content)} characters, {len(result.images)} images")

    return {
        "text": result.markdown_content,
        "images": [{"filename": img.filename, "data": img.data} for img in result.images],
        # Mammoth/Markitdown cannot provide a reliable DOCX page count because
        # pagination depends on Word layout.  Keep the established ``None``
        # contract instead of leaking a mock/provider-specific value.
        "page_count": None,
        "parser_name": "markitdown",
        "parser_version": "mammoth-direct-images/v2",
    }


def _mark_document_failed(document_id: str, error_msg: str, session_factory) -> None:
    """Update document status to failed in a fresh event loop.

    Called when SoftTimeLimitExceeded interrupts the main event loop,
    so we create a new loop to safely access the database.
    """
    cleanup_loop = asyncio.new_event_loop()
    try:
        async def _update():
            async with session_factory() as db:
                from sqlalchemy import select
                result = await db.execute(select(Document).where(Document.id == document_id))
                document = result.scalar_one_or_none()
                if document:
                    document.status = "failed"
                    document.parse_error = error_msg
                    await db.commit()
        cleanup_loop.run_until_complete(_update())
    except Exception as e:
        logger.error(f"Failed to mark document {document_id} as failed: {e}")
    finally:
        cleanup_loop.close()
