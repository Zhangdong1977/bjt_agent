"""Document parsing tasks."""

import asyncio
import json
import logging
import re
import redis
from pathlib import Path

from typing import Literal

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

def _publish_parse_progress(document_id: str, stage: str, processed: int, total: int, eta_seconds: int) -> None:
    """Publish a parse progress event to Redis Stream.

    Args:
        document_id: The document UUID
        stage: Stage name (e.g., "extracting_text", "saving")
        processed: Number of elements processed so far
        total: Total number of elements to process
        eta_seconds: Estimated seconds remaining
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
        pct = int(processed * 100 / total) if total > 0 else 0
        # logger.info(f"[PROGRESS] Publishing to {stream_key}: stage={stage}, processed={processed}, total={total}, pct={pct}%")
        r.xadd(stream_key, {"data": json.dumps(event)})
        # logger.info(f"[PROGRESS] Successfully published progress event for document {document_id}")
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

    docling_json = parsed_data.get("docling_json_path")
    if docling_json:
        document.docling_json_path = str(docling_json)

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

    logger.info(f"[PARSE] Starting parsing: document_id={document.id}, file_path={file_path}, type={suffix}")

    if suffix == ".pdf":
        _publish_parse_progress(document.id, "extracting_text", 0, 1, 0)
        parsed_data = await _parse_pdf_with_docling(file_path, document_id=document.id)
        _publish_parse_progress(document.id, "extracting_text", 1, 1, 0)
        logger.info(f"[PARSE] PDF parsing completed, markdown length: {len(parsed_data.get('text', ''))}")
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
        logger.info(f"[PARSE] DOCX parsing completed, markdown length: {len(parsed_data.get('text', ''))}")

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
    from backend.config import get_settings
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    settings = get_settings()

    # Create a fresh engine and session factory for this task to avoid event loop issues
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=5,
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
                return {"status": "error", "message": "Document not found"}

            document.status = "parsing"
            await db.flush()

            file_path = Path(document.file_path)
            if not file_path.exists():
                document.status = "failed"
                document.parse_error = "File not found"
                await db.flush()
                return {"status": "error", "message": "File not found"}

            try:
                result = await _parse_document_internal(document, file_path, settings)
                await db.commit()
                # Publish completion event after successful save and commit
                _publish_parse_progress(document.id, "completed", 1, 1, 0)
                return result
            except Exception as e:
                document.status = "failed"
                document.parse_error = str(e)
                await db.flush()
                await db.commit()
                return {"status": "error", "message": str(e)}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_parse())
    finally:
        loop.run_until_complete(engine.dispose())
        loop.close()


async def _parse_pdf_with_docling(file_path: Path, document_id: str = "") -> dict:
    """Parse PDF file using Docling converter.

    Produces Markdown with image file links + DoclingDocument JSON.
    OCR is disabled during parsing (deferred to review-time tools).

    Args:
        file_path: Path to the PDF file
        document_id: Document UUID for progress reporting

    Returns:
        Dict with text (Markdown), images, and page_count
    """
    from backend.parsers.docling_converter import DoclingConverter

    file_size = file_path.stat().st_size
    logger.info(f"Docling PDF parsing: {file_path} ({file_size / (1024*1024):.2f}MB)")

    images_dir = file_path.parent / f"{file_path.stem}_images"
    docling_json_path = file_path.parent / f"{file_path.stem}_docling.json"

    converter = DoclingConverter()
    result = await asyncio.to_thread(
        converter.convert, file_path, images_dir=images_dir, docling_json_path=docling_json_path
    )

    logger.info(
        f"Docling PDF conversion: {len(result.markdown_content)} chars, "
        f"{len(result.images)} images, {result.page_count} pages"
    )

    return {
        "text": result.markdown_content,
        "images": [{"filename": img.filename, "data": img.data} for img in result.images],
        "page_count": result.page_count,
        "docling_json_path": str(docling_json_path),
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
