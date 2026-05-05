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


class DirectFileImageHandler:
    """Writes DOCX images directly to disk, bypassing base64 encoding.

    Instead of letting mammoth base64-encode images into data URIs, this handler
    reads image bytes from the DOCX and writes them directly to the target directory,
    returning lightweight file-path references for the HTML/Markdown output.
    """

    def __init__(self, images_dir: Path, images_dir_name: str):
        self._images_dir = images_dir
        self._images_dir_name = images_dir_name
        self._counter = 0
        self.images: list[ImageInfo] = []

    def __call__(self, image):
        self._counter += 1
        ext = image.content_type.partition("/")[2] if image.content_type else "png"
        filename = f"image_{self._counter}.{ext}"

        self._images_dir.mkdir(parents=True, exist_ok=True)
        with image.open() as image_bytes:
            data = image_bytes.read()

        dest = self._images_dir / filename
        dest.write_bytes(data)

        self.images.append(ImageInfo(filename=filename, data=data))
        return {"src": f"{self._images_dir_name}/{filename}"}


class MarkitdownConverter:
    """Markitdown converter for DOCX/DOC files to Markdown format.

    Uses the markitdown library to extract text and images from documents.
    Images are written directly to disk via a custom mammoth image handler,
    avoiding the base64 round-trip overhead.
    """

    def __init__(self, timeout: int = 300):
        self.timeout = timeout

    def convert(self, file_path: Path, progress_callback=None, images_dir: Path = None) -> ConversionResult:
        if not file_path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        suffix = file_path.suffix.lower()
        if suffix not in [".docx", ".doc"]:
            raise ValueError(f"Unsupported file type: {suffix}. Expected .docx or .doc")

        file_size = file_path.stat().st_size
        logger.info(f"Markitdown conversion: {file_path} ({file_size / (1024 * 1024):.2f}MB)")

        if images_dir is None:
            images_dir = file_path.parent / f"{file_path.stem}_images"
        images_dir_name = images_dir.name

        try:
            markitdown_path = Path(__file__).parent.parent.parent / "third_party" / "markitdown" / "packages" / "markitdown" / "src"
            mammoth_path = Path(__file__).parent.parent.parent / "third_party" / "mammoth"

            for p in [str(markitdown_path), str(mammoth_path)]:
                if p not in sys.path:
                    sys.path.insert(0, p)

            import mammoth
            from markitdown import MarkItDown

            handler = DirectFileImageHandler(images_dir, images_dir_name)

            converter = MarkItDown()
            kwargs = {"convert_image": mammoth.images.img_element(handler)}
            if progress_callback:
                kwargs["progress_callback"] = progress_callback

            result = converter.convert(source=file_path, **kwargs)

            logger.info(f"Markitdown conversion successful: {len(result.markdown)} chars, {len(handler.images)} images")

            return ConversionResult(
                markdown_content=result.markdown or "",
                images=handler.images,
                page_count=None
            )

        except Exception as e:
            logger.error(f"Markitdown conversion failed: {e}")
            raise MarkitdownConversionError(f"Markitdown conversion failed: {e}")


def convert_to_markdown(file_path: Path) -> ConversionResult:
    converter = MarkitdownConverter()
    return converter.convert(file_path)
