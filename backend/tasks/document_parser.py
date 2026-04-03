"""Document parsing tasks."""

import asyncio
import base64
import logging
import re
from pathlib import Path

from backend.celery_app import celery_app
from backend.models import async_session_factory, Document
from backend.utils.text_utils import strip_ai_think_tags

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


async def _process_images_with_llm(images: list, api_key: str, api_base: str, model: str) -> list:
    """Process images with MiniMax LLM image understanding using MCP.

    Args:
        images: List of image info dicts with 'filename' and 'data'
        api_key: MiniMax API key (unused, MCP handles auth)
        api_base: API base URL (unused)
        model: Model name (unused)

    Returns:
        List of image descriptions
    """
    import sys
    import tempfile
    import os
    import json
    from pathlib import Path

    descriptions = []

    # Add Mini-Agent to path for MCP loading
    mini_agent_path = Path(__file__).parent.parent.parent / "Mini-Agent"
    if str(mini_agent_path) not in sys.path:
        sys.path.insert(0, str(mini_agent_path))

    from mini_agent.tools.mcp_loader import load_mcp_tools_async, cleanup_mcp_connections

    # Load MCP config and substitute env vars
    mcp_config_path = Path(__file__).parent.parent / "mcp.json"
    mcp_config = json.loads(mcp_config_path.read_text())
    mcp_config = await _substitute_env_vars(mcp_config)

    # Write substituted config to temp file for MCP loader
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        json.dump(mcp_config, tmp)
        config_tmp_path = tmp.name

    try:
        mcp_tools = await load_mcp_tools_async(config_tmp_path)

        understand_image_tool = None
        for tool in mcp_tools:
            if tool.name == "understand_image":
                understand_image_tool = tool
                break

        if not understand_image_tool:
            logger.warning("understand_image MCP tool not found, skipping image processing")
            await cleanup_mcp_connections()
            return descriptions

        for img_info in images[:5]:  # Limit to first 5 images
            img_tmp_path = None
            try:
                # Save image to temp file for MCP tool
                ext = img_info["filename"].split(".")[-1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
                    tmp.write(img_info["data"])
                    img_tmp_path = tmp.name

                # Call MCP understand_image tool
                result = await understand_image_tool.execute(
                    prompt="Extract all text content from this image. List each text item clearly. If there is no text, say 'No text content'.",
                    image_source=img_tmp_path,
                )

                if result.success and result.content:
                    # Clean up the result
                    description = strip_ai_think_tags(result.content)
                    # Remove markdown formatting (bold, italic, etc.)
                    description = re.sub(r'\*\*([^*]+)\*\*', r'\1', description)  # Bold
                    description = re.sub(r'\*([^*]+)\*', r'\1', description)  # Italic
                    description = re.sub(r'`([^`]+)`', r'\1', description)  # Inline code
                    # Remove code blocks
                    description = re.sub(r'^```\w*\n?', '', description)
                    description = re.sub(r'\n?```$', '', description)
                    description = description.strip()
                    descriptions.append(f"[Image: {img_info['filename']}] {description}")
                else:
                    error_msg = result.error or "Unknown error"
                    logger.warning(f"Image understanding failed for {img_info['filename']}: {error_msg}")
                    descriptions.append(f"[Image: {img_info['filename']}] (Image processing failed: {error_msg})")

            except Exception as e:
                logger.warning(f"Failed to process image {img_info['filename']}: {e}")
                descriptions.append(f"[Image: {img_info['filename']}] (Image processing failed: {e})")
            finally:
                if img_tmp_path and os.path.exists(img_tmp_path):
                    try:
                        os.unlink(img_tmp_path)
                    except Exception:
                        pass

        await cleanup_mcp_connections()
        return descriptions
    finally:
        if config_tmp_path and os.path.exists(config_tmp_path):
            try:
                os.unlink(config_tmp_path)
            except Exception:
                pass


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
