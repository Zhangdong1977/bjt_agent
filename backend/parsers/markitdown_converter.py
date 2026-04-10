"""Markitdown converter module for DOCX/DOC to Markdown conversion."""

import logging
import shutil
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

    def convert(self, file_path: Path) -> ConversionResult:
        """Convert a DOCX/DOC file to Markdown format.

        Args:
            file_path: Path to the input DOCX/DOC file

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
            # Add markitdown to path
            import sys
            markitdown_path = Path(__file__).parent.parent.parent / "third_party" / "markitdown" / "packages" / "markitdown" / "src"
            if str(markitdown_path) not in sys.path:
                sys.path.insert(0, str(markitdown_path))

            from markitdown import MarkItDown

            markitdown = MarkItDown()
            # keep_data_uris=True preserves full base64 image data instead of truncating to "base64..."
            result = markitdown.convert(str(file_path.resolve()), keep_data_uris=True)

            # Extract images from result
            images = self._extract_images(result)

            logger.info(f"Markitdown conversion successful: {len(result.text_content)} characters, {len(images)} images")

            return ConversionResult(
                markdown_content=result.text_content or "",
                images=images,
                page_count=None
            )

        except Exception as e:
            logger.error(f"Markitdown conversion failed: {e}")
            raise MarkitdownConversionError(f"Markitdown conversion failed: {e}")

    def _extract_images(self, result) -> list[ImageInfo]:
        """Extract images from markitdown result.

        Args:
            result: MarkItDown result object

        Returns:
            List of ImageInfo objects
        """
        images = []

        # markitdown stores images in result.images attribute
        # Each image has: path, name, data (bytes)
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