"""Docling converter module for PDF to Markdown conversion.

Uses Docling for PDF parsing with:
- do_ocr=False: Skip OCR during parsing for speed
- generate_picture_images=True: Extract images to separate files
- ImageRefMode.REFERENCED: Link to images in markdown (not base64)
- DoclingDocument JSON: Save structured data for review tools
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from backend.parsers.markitdown_converter import ConversionResult, ImageInfo

logger = logging.getLogger(__name__)


class DoclingConversionError(Exception):
    """Raised when Docling conversion fails."""
    pass


class DoclingConverter:
    """Docling-based PDF converter.

    Parses PDF documents using Docling, producing:
    1. Markdown with image file links (not base64, not OCR)
    2. Images saved to a subdirectory
    3. DoclingDocument JSON for structured tool access
    """

    def __init__(self, timeout: int = 600):
        self.timeout = timeout

    def convert(
        self,
        file_path: Path,
        images_dir: Path = None,
        docling_json_path: Path = None,
    ) -> ConversionResult:
        if not file_path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        suffix = file_path.suffix.lower()
        if suffix != ".pdf":
            raise ValueError(f"DoclingConverter only supports PDF, got {suffix}")

        file_size = file_path.stat().st_size
        logger.info(f"Docling PDF conversion: {file_path} ({file_size / (1024 * 1024):.2f}MB)")

        if images_dir is None:
            images_dir = file_path.parent / f"{file_path.stem}_images"
        if docling_json_path is None:
            docling_json_path = file_path.parent / f"{file_path.stem}_docling.json"

        try:
            return self._convert_pdf(file_path, images_dir, docling_json_path)
        except DoclingConversionError:
            raise
        except Exception as e:
            logger.error(f"Docling conversion failed: {e}")
            raise DoclingConversionError(f"Docling conversion failed: {e}") from e

    def _convert_pdf(
        self,
        file_path: Path,
        images_dir: Path,
        docling_json_path: Path,
    ) -> ConversionResult:
        docling_path = Path(__file__).parent.parent.parent / "third_party" / "docling"
        if str(docling_path) not in sys.path:
            sys.path.insert(0, str(docling_path))

        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc import ImageRefMode

        pipeline_options = PdfPipelineOptions(
            do_ocr=False,
            generate_picture_images=True,
            generate_page_images=False,
            images_scale=2.0,
        )

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            },
        )

        md_path = file_path.parent / f"{file_path.stem}_parsed.md"

        result = converter.convert(str(file_path))

        if result.status.name == "FAILURE":
            errors = "; ".join(str(e) for e in result.errors) if result.errors else "unknown"
            raise DoclingConversionError(f"Docling conversion failed: {errors}")

        page_count = len(result.pages) if result.pages else None

        md_path.parent.mkdir(parents=True, exist_ok=True)
        result.document.save_as_markdown(
            filename=str(md_path),
            artifacts_dir=str(images_dir),
            image_mode=ImageRefMode.REFERENCED,
        )

        markdown_content = md_path.read_text(encoding="utf-8")

        result.document.save_as_json(str(docling_json_path))

        images = []
        if images_dir.exists():
            for img_file in sorted(images_dir.iterdir()):
                if img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                    images.append(ImageInfo(filename=img_file.name, data=b""))

        logger.info(
            f"Docling PDF conversion: {len(markdown_content)} chars, "
            f"{len(images)} images, {page_count} pages"
        )

        return ConversionResult(
            markdown_content=markdown_content,
            images=images,
            page_count=page_count,
        )
