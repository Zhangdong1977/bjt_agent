"""Markitdown converter module for DOCX/DOC to Markdown conversion."""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MarkitdownConversionError(Exception):
    """Raised when markitdown conversion fails."""
    pass


@dataclass
class ImageInfo:
    """Image information extracted from document."""
    filename: str
    data: bytes


@dataclass
class ConversionResult:
    """Result of document conversion."""
    markdown_content: str
    images: list[ImageInfo]
    page_count: Optional[int] = None


class MarkitdownConverter:
    """Markitdown converter for DOCX/DOC files to Markdown format.

    Uses the markitdown library to extract text and images from documents.
    """

    def __init__(self, timeout: int = 300):
        """Initialize the converter.

        Args:
            timeout: Maximum time in seconds for conversion (default: 5 minutes)
        """
        self.timeout = timeout

    def convert(self, file_path: Path, progress_callback=None) -> ConversionResult:
        """Convert a DOCX/DOC file to Markdown format.

        Args:
            file_path: Path to the input DOCX/DOC file
            progress_callback: Optional callback for progress updates (processed, total)

        Returns:
            ConversionResult with markdown_content, images, and page_count

        Raises:
            MarkitdownConversionError: If conversion fails
            FileNotFoundError: If input file doesn't exist
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        suffix = file_path.suffix.lower()
        if suffix not in [".docx", ".doc"]:
            raise ValueError(f"Unsupported file type: {suffix}. Expected .docx or .doc")

        file_size = file_path.stat().st_size
        logger.info(f"Markitdown conversion: {file_path} ({file_size / (1024 * 1024):.2f}MB)")

        try:
            # Add markitdown and mammoth paths to sys.path
            markitdown_path = Path(__file__).parent.parent.parent / "third_party" / "markitdown" / "packages" / "markitdown" / "src"
            mammoth_path = Path(__file__).parent.parent.parent / "third_party" / "mammoth"

            for p in [str(markitdown_path), str(mammoth_path)]:
                if p not in sys.path:
                    sys.path.insert(0, p)

            # Import markitdown components
            from markitdown import MarkItDown

            # Create converter and convert
            converter = MarkItDown()

            # Prepare kwargs: keep_data_uris=True preserves full base64 image data
            # (without it, _CustomMarkdownify truncates to "data:image/png;base64,...")
            kwargs = {"keep_data_uris": True}
            if progress_callback:
                kwargs["progress_callback"] = progress_callback

            # Convert using markitdown (which uses mammoth internally)
            result = converter.convert(
                source=file_path,
                **kwargs
            )

            # Extract images from the markdown content (base64 data URIs)
            images = self._extract_images(result)

            logger.info(f"Markitdown conversion successful: {len(result.markdown)} chars, {len(images)} images")

            return ConversionResult(
                markdown_content=result.markdown or "",
                images=images,
                page_count=None
            )

        except Exception as e:
            logger.error(f"Markitdown conversion failed: {e}")
            raise MarkitdownConversionError(f"Markitdown conversion failed: {e}")

    def _extract_images(self, result) -> list[ImageInfo]:
        """Extract images from markitdown result.

        Since markitdown's DocumentConverterResult doesn't have a separate images attribute,
        images are embedded as base64 data URIs in the markdown text. This method extracts
        them from the text content.

        Args:
            result: MarkItDown DocumentConverterResult object

        Returns:
            List of ImageInfo objects
        """
        import base64
        import re

        images = []

        # First check if result has images attribute (some markitdown versions)
        if hasattr(result, 'images') and result.images:
            for img in result.images:
                if hasattr(img, 'data') and hasattr(img, 'name'):
                    images.append(ImageInfo(
                        filename=img.name,
                        data=img.data
                    ))
                elif isinstance(img, dict):
                    images.append(ImageInfo(
                        filename=img.get('name', 'image'),
                        data=img.get('data', b'')
                    ))
            return images

        # Fallback: extract images from markdown text content
        # The markdown contains data URIs like:
        #   ![alt](data:image/png;base64,iVBORw0KGgo...)
        markdown_text = result.markdown or ""

        # Pattern matches: data:image/[type];base64,[base64_data]
        # Uses [\s\S] to match across line boundaries since base64 can span multiple lines
        pattern = r'data:image/([^;]+);base64,([A-Za-z0-9+/=\s]+)'

        for match in re.finditer(pattern, markdown_text):
            mime_type = match.group(1)  # e.g., "png", "jpeg"
            base64_data = match.group(2).replace('\n', '').replace('\r', '').strip()

            try:
                image_data = base64.b64decode(base64_data)
                # Generate unique filename
                idx = len(images) + 1
                ext = mime_type.split('+')[0]  # Handle "jpeg+xml" etc
                filename = f"image_{idx}.{ext}"

                images.append(ImageInfo(
                    filename=filename,
                    data=image_data
                ))
            except Exception as e:
                logger.warning(f"Failed to decode base64 image: {e}")
                continue

        return images


# Module-level convenience function
def convert_to_markdown(file_path: Path) -> ConversionResult:
    """Convert a DOCX/DOC file to Markdown format.

    Args:
        file_path: Path to the input DOCX/DOC file

    Returns:
        ConversionResult with markdown_content, images, and page_count

    Raises:
        FileNotFoundError: If input file doesn't exist
        MarkitdownConversionError: If conversion fails
        ValueError: If file type is not supported
    """
    converter = MarkitdownConverter()
    return converter.convert(file_path)