"""Document parsing tasks."""

import asyncio
import json
import logging
import re
from pathlib import Path

from typing import Literal

from backend.celery_app import celery_app
from backend.models import Document

logger = logging.getLogger(__name__)


Stage = Literal["converting", "extracting", "saving"]

def _publish_parse_progress(document_id: str, stage: Stage, processed: int, total: int, eta_seconds: int) -> None:
    """Publish a parse progress event to Redis Stream.

    Args:
        document_id: The document UUID
        stage: One of "converting", "extracting", "saving"
        processed: Number of images processed so far
        total: Total number of images to process
        eta_seconds: Estimated seconds remaining
    """
    import redis
    from backend.config import get_settings

    try:
        settings = get_settings()
        r = redis.from_url(settings.redis_url)
        stream_key = f"sse:stream:doc_parse:{document_id}"
        event = {
            "type": "parse_progress",
            "stage": stage,
            "processed": processed,
            "total": total,
            "eta_seconds": eta_seconds,
        }
        r.xadd(stream_key, {"data": json.dumps(event)})
    except Exception as e:
        logger.warning(f"Failed to publish parse progress for {document_id}: {e}")


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
    parsed_dir = file_path.parent
    html_path = parsed_dir / f"{file_path.stem}_parsed.html"
    images_dir = parsed_dir / f"{file_path.stem}_images"

    html_content = parsed_data["text"]

    # Fix image paths in HTML to point to the images directory
    # MUST be done BEFORE embedding descriptions so that filenames match
    images_dir_name = f"{file_path.stem}_images"
    # For DOCX files, images are extracted to workspace_images_dir but parsed_data["images"]
    # may be empty if the file read failed, so check workspace_images_dir directly
    has_images = parsed_data["images"] or images_dir.exists()
    if has_images:
        html_content = _fix_html_image_paths(html_content, images_dir_name)
        # Also insert img tags for images that exist in directory but aren't referenced in HTML
        # This handles LibreOffice cases where images are extracted but not referenced in HTML
        html_content = _insert_missing_img_tags(html_content, images_dir)

    # Save images
    if parsed_data["images"]:
        images_dir.mkdir(exist_ok=True)
        for img_info in parsed_data["images"]:
            img_path = images_dir / img_info["filename"]
            img_path.write_bytes(img_info["data"])
        document.parsed_images_dir = str(images_dir)

    # Write HTML and update document
    html_path.write_text(html_content, encoding="utf-8")
    document.parsed_html_path = str(html_path)
    document.page_count = parsed_data.get("page_count")
    document.word_count = len(html_content.split())
    document.status = "parsed"

    return {
        "status": "success",
        "document_id": document.id,
        "parsed_html_path": str(html_path),
        "page_count": document.page_count,
        "word_count": document.word_count,
    }


async def _parse_document_internal(document: Document, file_path: Path, settings) -> dict:
    """Internal document parsing logic."""
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        _publish_parse_progress(document.id, "extracting", 0, 0, 0)
        parsed_data = await _parse_pdf(file_path)
    elif suffix in [".docx", ".doc"]:
        _publish_parse_progress(document.id, "converting", 0, 0, 0)
        parsed_data = await _parse_docx(file_path)
        _publish_parse_progress(document.id, "extracting", 0, 0, 0)
    else:
        document.status = "failed"
        document.parse_error = f"Unsupported file type: {suffix}"
        return {"status": "error", "message": f"Unsupported file type: {suffix}"}

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
                # Publish final progress: saving stage
                _publish_parse_progress(document_id, "saving", 0, 0, 0)
                await db.commit()
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


async def _parse_pdf(file_path: Path) -> dict:
    """Parse PDF file and extract text and images.

    Handles corrupted PDFs gracefully by catching exceptions.
    """
    import fitz  # PyMuPDF

    text_parts = []
    images = []
    page_count = 0

    try:
        doc = fitz.open(str(file_path))

        # Check if PDF is corrupted
        if doc.is_closed or doc.is_encrypted:
            raise ValueError(f"PDF is corrupted or encrypted: {file_path}")

        page_count = len(doc)

        # Limit page processing for very large documents
        max_pages = 500
        if page_count > max_pages:
            logger.warning(f"PDF has {page_count} pages, limiting to first {max_pages}")
            page_count = max_pages

        for page_num, page in enumerate(doc):
            if page_num >= max_pages:
                break

            try:
                text_parts.append(page.get_text())
            except Exception as e:
                logger.warning(f"Failed to extract text from page {page_num + 1}: {e}")
                text_parts.append(f"[Page {page_num + 1} text extraction failed]")

            # Extract images
            try:
                for img_index, img in enumerate(page.get_images()):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        # Limit image size to 10MB
                        if len(image_bytes) > 10 * 1024 * 1024:
                            logger.warning(f"Skipping large image ({len(image_bytes)} bytes) on page {page_num + 1}")
                            continue
                        image_filename = f"page_{page_num + 1}_img_{img_index + 1}.{image_ext}"
                        images.append({"filename": image_filename, "data": image_bytes})
                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_index} from page {page_num + 1}: {e}")
            except Exception as e:
                logger.warning(f"Failed to get images from page {page_num + 1}: {e}")

        doc.close()

    except Exception as e:
        logger.error(f"Failed to parse PDF {file_path}: {e}")
        raise ValueError(f"Failed to parse PDF: {e}")

    return {
        "text": "\n\n".join(text_parts),
        "images": images,
        "page_count": page_count,
    }


async def _parse_docx(file_path: Path) -> dict:
    """Parse DOCX file using LibreOffice in HTML mode.

    Uses LibreOffice to convert DOCX to HTML. Returns the HTML content directly
    without converting to Markdown, preserving full HTML structure and reducing
    information loss.

    Args:
        file_path: Path to the DOCX file

    Returns:
        Dict with text (HTML), images, and page_count (None)

    Raises:
        ValueError: If LibreOffice conversion fails
    """
    import shutil
    from backend.parsers import LibreOfficeConverter

    file_size = file_path.stat().st_size
    logger.info(f"LibreOffice HTML parsing: {file_path} ({file_size / (1024*1024):.2f}MB)")

    # Convert DOCX to HTML using the parser module
    converter = LibreOfficeConverter()
    result = await converter.convert(file_path)

    html_content = result["text"]
    lo_images_dir = result["images_dir"]  # LibreOffice temp directory with extracted images

    # Clean up sd-abs-pos elements (LibreOffice absolute positioning artifacts)
    # These elements cause layout issues in web view as they use absolute positioning
    # that doesn't flow with the document structure
    html_content = _clean_sd_abs_pos_elements(html_content)

    logger.info(f"LibreOffice HTML conversion successful: {len(html_content)} characters")

    # Determine the target images directory in workspace
    workspace_images_dir = file_path.parent / f"{file_path.stem}_images"

    # Copy images from LibreOffice temp directory to workspace
    if lo_images_dir and lo_images_dir.exists():
        workspace_images_dir.mkdir(parents=True, exist_ok=True)
        for img_path in lo_images_dir.iterdir():
            if img_path.is_file() and img_path.suffix.lower() in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"]:
                dest_path = workspace_images_dir / img_path.name
                if not dest_path.exists():
                    shutil.copy2(img_path, dest_path)
        logger.info(f"Copied images from {lo_images_dir} to {workspace_images_dir}")

    # Extract images from the workspace images directory
    images = []
    if workspace_images_dir.exists():
        for img_path in workspace_images_dir.iterdir():
            if img_path.is_file() and img_path.suffix.lower() in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"]:
                try:
                    # Limit image size to 10MB
                    if img_path.stat().st_size > 10 * 1024 * 1024:
                        logger.warning(f"Skipping large image ({img_path.stat().st_size} bytes): {img_path.name}")
                        continue
                    images.append({
                        "filename": img_path.name,
                        "data": img_path.read_bytes(),
                    })
                except Exception as e:
                    logger.warning(f"Failed to read image {img_path}: {e}")

    logger.info(f"Extracted {len(images)} images from DOCX")

    return {
        "text": html_content,
        "images": images,
        "page_count": None,
    }
