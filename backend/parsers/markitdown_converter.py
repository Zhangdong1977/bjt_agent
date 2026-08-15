"""Markitdown converter module for DOCX/DOC/PDF to Markdown conversion."""

import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 1x1 transparent PNG substituted for DOCX images whose part is missing or
# unreadable inside the archive (e.g. bid-authoring tools emitting image
# relationships with Target="../NULL").
_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


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
        self.failed_images = 0

    def __call__(self, image):
        self._counter += 1
        content_type = str(image.content_type or "").lower()
        ext = {
            "image/jpeg": "jpeg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/bmp": "bmp",
            "image/tiff": "tiff",
            "image/svg+xml": "svg",
            "image/jp2": "jp2",
            "image/jpx": "jpx",
        }.get(content_type, content_type.partition("/")[2] or "png")
        ext = ext.split("+", 1)[0]
        filename = f"image_{self._counter}.{ext}"

        self._images_dir.mkdir(parents=True, exist_ok=True)
        try:
            with image.open() as image_bytes:
                data = image_bytes.read()
        except Exception as e:
            # Some authoring tools emit image relationships pointing at parts
            # that don't exist in the archive (Target="../NULL"); mammoth
            # surfaces that as KeyError from zipfile.  One broken image must
            # not fail the whole conversion: substitute a 1x1 placeholder so
            # markdown refs, files on disk and the images list stay consistent.
            logger.warning(
                "DOCX image %s could not be read (%s); substituting 1x1 placeholder",
                filename,
                e,
            )
            self.failed_images += 1
            filename = f"image_{self._counter}.png"
            data = _PLACEHOLDER_PNG

        dest = self._images_dir / filename
        dest.write_bytes(data)

        self.images.append(ImageInfo(filename=filename, data=data))
        return {"src": f"{self._images_dir_name}/{filename}"}


class MarkitdownConverter:
    """Markitdown converter for DOCX/DOC/PDF files to Markdown format.

    Uses the markitdown library to extract text and images from documents.
    For DOCX/DOC, images are written directly to disk via a custom mammoth image handler.
    For PDF, uses markitdown's built-in PdfConverter for text/tables and PyMuPDF for images.
    """

    def __init__(self, timeout: int = 300):
        self.timeout = timeout

    def convert(self, file_path: Path, progress_callback=None, images_dir: Path = None) -> ConversionResult:
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在，请重新上传：{file_path}")

        suffix = file_path.suffix.lower()
        if suffix not in [".docx", ".doc", ".pdf"]:
            raise ValueError(f"暂不支持 {suffix or '未知'} 格式，请上传 DOCX、DOC 或 PDF 文件")

        file_size = file_path.stat().st_size
        logger.info(f"Markitdown conversion: {file_path} ({file_size / (1024 * 1024):.2f}MB)")

        if images_dir is None:
            images_dir = file_path.parent / f"{file_path.stem}_images"

        try:
            if suffix in [".docx", ".doc"]:
                return self._convert_docx(file_path, images_dir, progress_callback)
            else:
                return self._convert_pdf(file_path, images_dir, progress_callback)

        except MarkitdownConversionError:
            raise
        except Exception as e:
            logger.error(f"Markitdown conversion failed: {e}")
            raise MarkitdownConversionError(f"文档转换失败：{e}")

    def _convert_docx(self, file_path: Path, images_dir: Path, progress_callback) -> ConversionResult:
        """Convert DOCX/DOC while materializing every embedded image.

        MarkItDown's DOCX converter accepts arbitrary keyword arguments but its
        current implementation does not forward ``convert_image`` to Mammoth.
        Calling it therefore produces ``data:image/...;base64`` Markdown and
        leaves the image directory empty.  Invoke Mammoth directly, then apply
        the same HTML-to-Markdown stage so image references remain lightweight
        paths backed by real files.
        """
        import mammoth
        from markdownify import markdownify

        images_dir_name = images_dir.name
        handler = DirectFileImageHandler(images_dir, images_dir_name)
        with file_path.open("rb") as source:
            html_result = mammoth.convert_to_html(
                source,
                convert_image=mammoth.images.img_element(handler),
            )
        for message in html_result.messages:
            logger.warning("Mammoth DOCX conversion warning: %s", message.message)
        markdown_content = markdownify(
            html_result.value,
            heading_style="ATX",
            bullets="-",
        ).strip()
        if progress_callback:
            progress_callback(1, 1)

        logger.info(
            "DOCX conversion successful: %s chars, %s materialized images",
            len(markdown_content),
            len(handler.images),
        )
        if handler.failed_images:
            logger.warning(
                "DOCX contained %s unreadable image(s); substituted 1x1 placeholder(s)",
                handler.failed_images,
            )

        return ConversionResult(
            markdown_content=markdown_content,
            images=handler.images,
            page_count=None,
        )

    def _convert_pdf(self, file_path: Path, images_dir: Path, progress_callback=None) -> ConversionResult:
        """Convert PDF file to Markdown using PyMuPDF page-by-page extraction.

        Extracts text and images per page, inserting image references inline
        after each page's text content.
        """
        import fitz

        images_dir.mkdir(parents=True, exist_ok=True)
        images_dir_name = images_dir.name

        page_parts: list[str] = []
        all_images: list[ImageInfo] = []

        doc = fitz.open(str(file_path))
        if doc.is_encrypted:
            doc.close()
            raise MarkitdownConversionError("PDF 文件已加密，请解除密码后重新上传")

        total_pages = len(doc)
        logger.info(f"PDF fitz conversion: {file_path.name}, {total_pages} pages")

        for page_num in range(total_pages):
            page = doc[page_num]
            page_text = page.get_text().strip()
            page_image_refs: list[str] = []

            try:
                for img_index, img in enumerate(page.get_images()):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        if len(image_bytes) > 10 * 1024 * 1024:
                            continue
                        filename = f"page_{page_num + 1}_img_{img_index + 1}.{image_ext}"
                        (images_dir / filename).write_bytes(image_bytes)
                        all_images.append(ImageInfo(filename=filename, data=image_bytes))
                        page_image_refs.append(f"![{filename}]({images_dir_name}/{filename})")
                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_index} from page {page_num + 1}: {e}")
            except Exception as e:
                logger.warning(f"Failed to get images from page {page_num + 1}: {e}")

            if page_text:
                page_parts.append(page_text)
            page_parts.extend(page_image_refs)

            if progress_callback:
                progress_callback(page_num + 1, total_pages)

        doc.close()

        markdown_content = "\n\n".join(page_parts)
        logger.info(f"PDF fitz conversion result: {len(markdown_content)} chars, {len(all_images)} images, {total_pages} pages")

        return ConversionResult(
            markdown_content=markdown_content,
            images=all_images,
            page_count=total_pages,
        )


def convert_to_markdown(file_path: Path) -> ConversionResult:
    converter = MarkitdownConverter()
    return converter.convert(file_path)


def _extract_pdf_text_with_fitz(file_path: Path) -> str:
    """Extract text from PDF using PyMuPDF as a fallback when markitdown fails."""
    import fitz

    text_parts = []
    try:
        doc = fitz.open(str(file_path))
        if doc.is_encrypted:
            doc.close()
            raise ValueError("PDF 文件已加密，请解除密码后重新上传")

        max_pages = 500
        for page_num in range(min(len(doc), max_pages)):
            page = doc[page_num]
            try:
                text = page.get_text()
                if text and text.strip():
                    text_parts.append(text.strip())
            except Exception as e:
                logger.warning(f"Failed to extract text from page {page_num + 1}: {e}")
        doc.close()
    except Exception as e:
        logger.error(f"PyMuPDF text extraction failed for {file_path}: {e}")
        raise

    return "\n\n".join(text_parts)


def _extract_pdf_images(file_path: Path, images_dir: Path) -> list[ImageInfo]:
    """Extract embedded images from PDF using PyMuPDF."""
    import fitz

    images: list[ImageInfo] = []

    try:
        doc = fitz.open(str(file_path))
        if doc.is_encrypted:
            logger.warning(f"PDF is encrypted, skipping image extraction: {file_path}")
            doc.close()
            return images

        max_pages = 500
        for page_num in range(min(len(doc), max_pages)):
            page = doc[page_num]
            try:
                for img_index, img in enumerate(page.get_images()):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        if len(image_bytes) > 10 * 1024 * 1024:
                            continue
                        filename = f"page_{page_num + 1}_img_{img_index + 1}.{image_ext}"
                        images_dir.mkdir(parents=True, exist_ok=True)
                        (images_dir / filename).write_bytes(image_bytes)
                        images.append(ImageInfo(filename=filename, data=image_bytes))
                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_index} from page {page_num + 1}: {e}")
            except Exception as e:
                logger.warning(f"Failed to get images from page {page_num + 1}: {e}")

        doc.close()
    except Exception as e:
        logger.warning(f"PDF image extraction failed for {file_path}: {e}")

    return images


def _get_pdf_page_count(file_path: Path) -> Optional[int]:
    """Get PDF page count using PyMuPDF."""
    import fitz

    try:
        doc = fitz.open(str(file_path))
        count = len(doc)
        doc.close()
        return count
    except Exception as e:
        logger.warning(f"Failed to get PDF page count: {e}")
        return None
