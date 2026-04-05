"""Document parsing tasks."""

import asyncio
import base64
import json
import logging
import re
import time
from pathlib import Path

from typing import Literal

from backend.celery_app import celery_app
from backend.models import async_session_factory, Document
from backend.utils.text_utils import strip_ai_think_tags

logger = logging.getLogger(__name__)


Stage = Literal["extracting_text", "processing_images", "saving"]

def _publish_parse_progress(document_id: str, stage: Stage, processed: int, total: int, eta_seconds: int) -> None:
    """Publish a parse progress event to Redis Stream.

    Args:
        document_id: The document UUID
        stage: One of "extracting_text", "processing_images", "saving"
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


def _embed_image_descriptions_in_md(md_content: str, desc_map: dict[str, str]) -> str:
    """Embed image descriptions below their corresponding image links in markdown.

    Args:
        md_content: Markdown content with ![image](path) patterns
        desc_map: Mapping of filename -> description text

    Returns:
        Markdown with descriptions embedded below image links
    """
    from urllib.parse import unquote

    def replace_image_match(match):
        image_path = match.group(1)  # e.g., "RTCMS_images/xxx.png" (may be URL-encoded)
        filename = Path(unquote(image_path)).name  # Extract "xxx.png" and URL-decode
        desc = desc_map.get(filename, "")
        if desc:
            return f"{match.group(0)}\n图片内容: {desc}"
        return match.group(0)

    return re.sub(r'!\[image\]\(([^)]+)\)', replace_image_match, md_content)


def _embed_image_descriptions_in_html(html_content: str, desc_map: dict[str, str]) -> str:
    """Embed image descriptions below their corresponding image tags in HTML.

    Args:
        html_content: HTML content with <img> tags
        desc_map: Mapping of filename -> description text

    Returns:
        HTML with descriptions embedded below image tags
    """
    import html
    from urllib.parse import unquote

    def replace_img_match(match):
        img_tag = match.group(0)
        # Extract src attribute
        src_match = re.search(r'src=["\']([^"\']+)["\']', img_tag)
        if not src_match:
            return img_tag
        src = src_match.group(1)
        # Try to find matching description by checking if any desc_map key
        # ends with the filename part of the src path
        # This handles URL-encoding mismatches between HTML and actual filenames
        src_filename = Path(src).name
        desc = ""
        for key, value in desc_map.items():
            if key.endswith(src_filename) or src_filename.endswith(key):
                desc = value
                break
        if not desc:
            # Try URL-decoding the src filename
            decoded_filename = Path(unquote(src_filename)).name
            desc = desc_map.get(decoded_filename, "")
        if desc:
            # First escape HTML special characters (& < >), then escape backslashes
            # to prevent issues with \xNN sequences in the content
            escaped_desc = html.escape(desc, quote=False).replace('\\', '&#92;')
            return f'{img_tag}<p>图片内容: {escaped_desc}</p>'
        return img_tag

    return re.sub(r'<img[^>]+>', replace_img_match, html_content)


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


async def _save_parsed_content(file_path: Path, parsed_data: dict, document: Document, settings, document_id: str) -> dict:
    """Save parsed content to disk and update document record."""
    parsed_dir = file_path.parent
    html_path = parsed_dir / f"{file_path.stem}_parsed.html"
    images_dir = parsed_dir / f"{file_path.stem}_images"

    html_content = parsed_data["text"]

    # Process images with LLM if available
    desc_map = {}
    if parsed_data["images"] and settings.mini_agent_api_key:
        try:
            image_descriptions = await _process_images_with_llm(
                parsed_data["images"],
                settings.mini_agent_api_key,
                settings.mini_agent_api_base,
                settings.mini_agent_model,
                document_id,
            )
            if image_descriptions:
                # Build filename -> description mapping
                for desc in image_descriptions:
                    # Format: "[Image: filename.png] 描述内容"
                    # Use re.DOTALL so that .+ matches newlines in multi-line descriptions
                    match = re.match(r'\[Image: ([^\]]+)\] (.+)', desc, re.DOTALL)
                    if match:
                        desc_map[match.group(1)] = match.group(2)
        except Exception as e:
            logger.warning(f"Failed to process images with LLM: {e}")

    # Fix image paths in HTML to point to the images directory
    # MUST be done BEFORE embedding descriptions so that filenames match
    images_dir_name = f"{file_path.stem}_images"
    if parsed_data["images"]:
        html_content = _fix_html_image_paths(html_content, images_dir_name)

    # Embed descriptions below corresponding image links in HTML
    # This must happen AFTER path fixing so that filenames in HTML match desc_map keys
    if desc_map:
        html_content = _embed_image_descriptions_in_html(html_content, desc_map)

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
        parsed_data = await _parse_pdf(file_path)
    elif suffix in [".docx", ".doc"]:
        parsed_data = await _parse_docx(file_path)
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

            # Publish initial progress: extracting text stage
            _publish_parse_progress(document_id, "extracting_text", 0, 0, 0)

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


async def _substitute_env_vars(config: dict) -> dict:
    """Substitute ${ENV_VAR} patterns in config with actual environment variable values.

    Handles both top-level config["env"] and nested config["mcpServers"][server]["env"] structures.

    Args:
        config: MCP server config dict

    Returns:
        Config with env vars substituted
    """
    import os
    import re

    def substitute_env(env: dict) -> dict:
        """Substitute env vars in a single env dict."""
        if not env:
            return env
        env = dict(env)  # Copy
        for key, value in env.items():
            if isinstance(value, str):
                # Match ${VAR} pattern
                matches = re.findall(r'\$\{([^}]+)\}', value)
                for var_name in matches:
                    env[key] = value.replace(f'${{{var_name}}}', os.environ.get(var_name, ""))
        return env

    config = dict(config)  # Shallow copy

    # Handle top-level env
    if "env" in config and config["env"]:
        config["env"] = substitute_env(config["env"])

    # Handle nested mcpServers structure
    if "mcpServers" in config and config["mcpServers"]:
        config["mcpServers"] = dict(config["mcpServers"])
        for server_name, server_config in config["mcpServers"].items():
            if isinstance(server_config, dict) and "env" in server_config:
                config["mcpServers"][server_name] = dict(server_config)
                config["mcpServers"][server_name]["env"] = substitute_env(server_config["env"])

    return config


async def _process_images_with_llm(images: list, api_key: str, api_base: str, model: str, document_id: str) -> list:
    """Process images with MiniMax LLM image understanding using direct API.

    Args:
        images: List of image info dicts with 'filename' and 'data'
        api_key: MiniMax API key
        api_base: API base URL
        model: Model name (unused, kept for API compatibility)
        document_id: Document UUID for progress tracking

    Returns:
        List of image descriptions
    """
    import html
    import httpx
    import base64

    total_images = len(images)
    processed_count = 0
    start_time = time.time()
    PROGRESS_PUBLISH_INTERVAL = 10  # Publish every 10 images

    descriptions = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        for img_info in images:
            try:
                # Encode image as base64
                img_b64 = base64.b64encode(img_info["data"]).decode()

                # Determine MIME type from filename
                filename = img_info["filename"]
                ext = filename.split(".")[-1].lower()
                mime_types = {
                    "png": "image/png",
                    "jpg": "image/jpeg",
                    "jpeg": "image/jpeg",
                    "gif": "image/gif",
                    "webp": "image/webp",
                }
                mime_type = mime_types.get(ext, "image/png")

                # Call MiniMax coding plan VLM API endpoint
                response = await client.post(
                    f"{api_base}/v1/coding_plan/vlm",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "prompt": "Extract all text content from this image. List each text item clearly. If there is no text, say 'No text content'.",
                        "image_url": f"data:{mime_type};base64,{img_b64}",
                    },
                )

                if response.status_code == 200:
                    result = response.json()
                    # Log raw response for debugging
                    logger.info(f"Image understanding API response for {filename}: {result}")
                    # Try multiple response formats: {"content": ...} or {"choices": [{"message": {"content": ...}}]}
                    content = result.get("content", "")
                    if not content and "choices" in result:
                        choices = result["choices"]
                        if choices and isinstance(choices, list):
                            first_choice = choices[0]
                            if isinstance(first_choice, dict):
                                message = first_choice.get("message", {})
                                content = message.get("content", "")
                    if content:
                        # Log raw content for debugging
                        logger.info(f"Raw content from API for {filename}: {repr(content)[:500]}")
                        # Decode HTML entities (e.g., &#92; -> \) before regex processing
                        # to prevent conflicts with Python escape sequences in regex replacements
                        content = html.unescape(content)
                        logger.info(f"After html.unescape for {filename}: {repr(content)[:500]}")
                        description = strip_ai_think_tags(content)
                        logger.info(f"After strip_ai_think_tags for {filename}: {repr(description)[:500]}")
                        # Remove bold markers **text** -> text (function-based to avoid escape issues)
                        description = re.sub(r'\*\*([^*]+)\*\*', lambda m: m.group(1), description, flags=re.DOTALL)
                        # Remove italic markers *text* -> text (don't match asterisks at line start)
                        description = re.sub(r'(?<!\n)\*([^*\n]+)\*(?!\n)', lambda m: m.group(1), description)
                        # Remove inline code
                        description = re.sub(r'`([^`]+)`', lambda m: m.group(1), description)
                        # Remove code blocks
                        description = re.sub(r'^```\w*\n?', '', description, flags=re.MULTILINE)
                        description = re.sub(r'\n?```$', '', description)
                        description = description.strip()
                        # Log description before appending for debugging
                        logger.info(f"Final description for {filename}: {repr(description)[:500]}")
                        descriptions.append(f"[Image: {filename}] {description}")
                        processed_count += 1
                        if processed_count % PROGRESS_PUBLISH_INTERVAL == 0 or processed_count == total_images:
                            elapsed = time.time() - start_time
                            avg_time = elapsed / processed_count
                            remaining = total_images - processed_count
                            eta_seconds = int(remaining * avg_time)
                            _publish_parse_progress(
                                document_id,
                                "processing_images",
                                processed_count,
                                total_images,
                                eta_seconds,
                            )
                    else:
                        descriptions.append(f"[Image: {filename}] (No text content)")
                        processed_count += 1
                        if processed_count % PROGRESS_PUBLISH_INTERVAL == 0 or processed_count == total_images:
                            elapsed = time.time() - start_time
                            avg_time = elapsed / processed_count
                            remaining = total_images - processed_count
                            eta_seconds = int(remaining * avg_time)
                            _publish_parse_progress(
                                document_id,
                                "processing_images",
                                processed_count,
                                total_images,
                                eta_seconds,
                            )
                else:
                    error_msg = response.text
                    logger.warning(f"Image understanding API failed for {filename}: {response.status_code} - {error_msg}")
                    descriptions.append(f"[Image: {filename}] (Image processing failed: {response.status_code})")
                    processed_count += 1
                    if processed_count % PROGRESS_PUBLISH_INTERVAL == 0 or processed_count == total_images:
                        elapsed = time.time() - start_time
                        avg_time = elapsed / processed_count
                        remaining = total_images - processed_count
                        eta_seconds = int(remaining * avg_time)
                        _publish_parse_progress(
                            document_id,
                            "processing_images",
                            processed_count,
                            total_images,
                            eta_seconds,
                        )

            except Exception as e:
                logger.warning(f"Failed to process image {img_info['filename']}: {e}")
                logger.warning(f"Exception type: {type(e).__name__}")
                # Check if filename contains problematic characters
                logger.warning(f"Filename repr: {repr(img_info['filename'])}")
                descriptions.append(f"[Image: {img_info['filename']}] (Image processing failed: {e})")
                processed_count += 1
                if processed_count % PROGRESS_PUBLISH_INTERVAL == 0 or processed_count == total_images:
                    elapsed = time.time() - start_time
                    avg_time = elapsed / processed_count
                    remaining = total_images - processed_count
                    eta_seconds = int(remaining * avg_time)
                    _publish_parse_progress(
                        document_id,
                        "processing_images",
                        processed_count,
                        total_images,
                        eta_seconds,
                    )

    return descriptions


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
