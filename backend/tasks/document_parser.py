"""Document parsing tasks."""

import asyncio
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
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
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


async def _save_parsed_content(file_path: Path, parsed_data: dict, document: Document, settings, document_id: str) -> dict:
    """Save parsed content to disk and update document record."""
    _publish_parse_progress(document_id, "saving", 1, 3, 0)

    parsed_dir = file_path.parent
    md_path = parsed_dir / f"{file_path.stem}_parsed.md"
    images_dir = parsed_dir / f"{file_path.stem}_images"

    md_content = parsed_data["text"]
    md_path.write_text(md_content, encoding="utf-8")

    # Images are already on disk (written by DirectFileImageHandler during parsing)
    if parsed_data["images"]:
        document.parsed_images_dir = str(images_dir)

    document.parsed_markdown_path = str(md_path)
    document.word_count = len(md_content.split())
    document.page_count = parsed_data.get("page_count")

    document.status = "parsed"

    _publish_parse_progress(document_id, "saving", 3, 3, 0)

    return {
        "status": "success",
        "document_id": document.id,
        "parsed_markdown_path": str(md_path),
        "page_count": document.page_count,
        "word_count": document.word_count,
    }


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

    # Check for scanned PDF (empty/very short content)
    md_text = parsed_data.get("text", "")
    if suffix == ".pdf" and len(md_text.strip()) < 100:
        raise ValueError("该 PDF 似乎为扫描件（无可提取文字），暂不支持 OCR，请上传文字版 PDF 或 Word 文档")

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
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=5,
        pool_recycle=1800,
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

            document.status = "parsing"
            await db.flush()

            file_path = Path(document.file_path)
            if not file_path.exists():
                document.status = "failed"
                document.parse_error = "文件不存在，请重新上传"
                await db.flush()
                logger.error(f"[PARSE] File not found on disk: {file_path}")
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
        "page_count": result.page_count,
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
