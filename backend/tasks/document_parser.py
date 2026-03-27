"""Document parsing tasks."""

import asyncio
import base64
import logging
from pathlib import Path

from backend.celery_app import celery_app
from backend.models import async_session_factory, Document

logger = logging.getLogger(__name__)


async def _save_parsed_content(file_path: Path, parsed_data: dict, document: Document, settings) -> dict:
    """Save parsed content to disk and update document record."""
    parsed_dir = file_path.parent
    md_path = parsed_dir / f"{file_path.stem}_parsed.md"
    images_dir = parsed_dir / f"{file_path.stem}_images"

    md_content = parsed_data["text"]

    # Process images with LLM if available
    if parsed_data["images"] and settings.mini_agent_api_key:
        try:
            image_descriptions = await _process_images_with_llm(
                parsed_data["images"],
                settings.mini_agent_api_key,
                settings.mini_agent_api_base,
                settings.mini_agent_model,
            )
            if image_descriptions:
                md_content += "\n\n## Extracted Image Content\n\n"
                md_content += "\n".join([f"- {desc}" for desc in image_descriptions])
        except Exception as e:
            logger.warning(f"Failed to process images with LLM: {e}")

    # Save images
    if parsed_data["images"]:
        images_dir.mkdir(exist_ok=True)
        for img_info in parsed_data["images"]:
            img_path = images_dir / img_info["filename"]
            img_path.write_bytes(img_info["data"])
        document.parsed_images_dir = str(images_dir)

    # Write markdown and update document
    md_path.write_text(md_content, encoding="utf-8")
    document.parsed_md_path = str(md_path)
    document.page_count = parsed_data.get("page_count")
    document.word_count = len(md_content.split())
    document.status = "parsed"

    return {
        "status": "success",
        "document_id": document.id,
        "parsed_md_path": str(md_path),
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

    return await _save_parsed_content(file_path, parsed_data, document, settings)


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
                return result
            except Exception as e:
                document.status = "failed"
                document.parse_error = str(e)
                await db.flush()
                await db.commit()
                return {"status": "error", "message": str(e)}

    try:
        return asyncio.run(_parse())
    finally:
        # Dispose the engine when done
        asyncio.run(engine.dispose())


async def _process_images_with_llm(images: list, api_key: str, api_base: str, model: str) -> list:
    """Process images with MiniMax LLM image understanding.

    Args:
        images: List of image info dicts with 'filename' and 'data'
        api_key: MiniMax API key
        api_base: API base URL
        model: Model name

    Returns:
        List of image descriptions
    """
    import httpx

    descriptions = []

    # MiniMax uses OpenAI-compatible API for vision
    # API endpoint: https://api.minimaxi.com/v1/images/generations (for generation)
    # For understanding, we use chat completions with image URLs

    for img_info in images[:5]:  # Limit to first 5 images to avoid excessive API calls
        try:
            # Convert image bytes to base64
            image_base64 = base64.b64encode(img_info["data"]).decode("utf-8")
            image_ext = img_info["filename"].split(".")[-1]
            mime_type = f"image/{image_ext}" if image_ext in ["png", "jpeg", "jpg", "gif", "webp"] else "image/png"

            # Call MiniMax vision API (OpenAI-compatible format)
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{api_base}/v1/chat/completions",
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
                                        "text": "Describe this image in detail. Focus on any text, diagrams, tables, or important visual elements that might be relevant for a document review. If there is no meaningful content, say 'No significant content'."
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:{mime_type};base64,{image_base64}"
                                        }
                                    }
                                ]
                            }
                        ],
                        "max_tokens": 500,
                    },
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("choices") and len(data["choices"]) > 0:
                        description = data["choices"][0]["message"]["content"]
                        descriptions.append(f"[Image: {img_info['filename']}] {description}")
                else:
                    logger.warning(f"Image understanding API error: {response.status_code} - {response.text}")

        except Exception as e:
            logger.warning(f"Failed to process image {img_info['filename']}: {e}")
            descriptions.append(f"[Image: {img_info['filename']}] (Image processing failed)")

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

    Uses LibreOffice to convert DOCX to HTML, then parses the HTML structure
    to generate structured Markdown with proper heading, list, and table
    formatting.

    Args:
        file_path: Path to the DOCX file

    Returns:
        Dict with text (markdown), images, and page_count (None)

    Raises:
        ValueError: If LibreOffice conversion fails
    """
    import shutil
    from backend.parsers import LibreOfficeConverter, html_to_markdown

    file_size = file_path.stat().st_size
    logger.info(f"LibreOffice HTML parsing: {file_path} ({file_size / (1024*1024):.2f}MB)")

    # Convert DOCX to HTML using the parser module
    converter = LibreOfficeConverter()
    result = await converter.convert(file_path)

    html_content = result["text"]
    lo_images_dir = result["images_dir"]  # LibreOffice temp directory with extracted images

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

    # Convert HTML to structured Markdown
    # images_base_path is relative path from markdown file to images directory
    images_base_path = f"{file_path.stem}_images"
    markdown_text = html_to_markdown(html_content, workspace_images_dir, images_base_path)
    logger.info(f"HTML to Markdown conversion successful: {len(markdown_text)} characters")

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
        "text": markdown_text,
        "images": images,
        "page_count": None,
    }
