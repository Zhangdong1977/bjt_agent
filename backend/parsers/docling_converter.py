"""Docling converter module for PDF to Markdown conversion.

Uses Docling for PDF parsing with:
- do_ocr=False: Skip OCR during parsing for speed
- do_table_structure=True: High-quality table extraction
- generate_picture_images=True: Extract images to separate files
- ImageRefMode.REFERENCED: Link to images in markdown (not base64)
- DoclingDocument JSON: Save structured data for review tools
- ProgressReportingPdfPipeline: Page-level progress callback
"""

import gc
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

from backend.parsers.markitdown_converter import ConversionResult, ImageInfo

logger = logging.getLogger(__name__)


class DoclingConversionError(Exception):
    """Raised when Docling conversion fails."""
    pass


class ProgressReportingPdfPipeline:
    """Factory that creates a StandardPdfPipeline subclass with multi-stage progress.

    Hooks _postprocess on all active pipeline stages (preprocess, layout, table,
    assemble — ocr is skipped when do_ocr=False). The callback receives
    (stage_name, page_no) so the caller can compute weighted progress.

    The pipeline class is created once and cached; the callback is read dynamically
    from the class attribute on each pipeline run, so it can be updated between calls
    without recreating the class or the heavy models it carries.

    Usage:
        ProgressReportingPdfPipeline.set_callback(my_callback)
        cls = ProgressReportingPdfPipeline.get_pipeline_class()
        # Pass cls as pipeline_cls to PdfFormatOption
    """

    _callback: Callable[[str, int], None] | None = None
    _pipeline_cls: type | None = None
    _page_images_dir: Path | None = None

    @classmethod
    def set_callback(cls, callback: Callable[[str, int], None] | None):
        cls._callback = callback

    @classmethod
    def set_page_images_dir(cls, dir: Path | None):
        cls._page_images_dir = dir

    @classmethod
    def get_pipeline_class(cls):
        """Return a cached StandardPdfPipeline subclass that reads callback dynamically."""
        if cls._pipeline_cls is not None:
            return cls._pipeline_cls

        docling_path = Path(__file__).parent.parent.parent / "third_party" / "docling"
        if str(docling_path) not in sys.path:
            sys.path.insert(0, str(docling_path))

        from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline

        owner = cls  # reference to ProgressReportingPdfPipeline for dynamic lookup

        class _ProgressPipeline(StandardPdfPipeline):

            def _release_page_resources(self, item) -> None:
                """Override: save page image to disk, then release from memory.

                After each page clears the assemble stage, its full-resolution image
                (≈2 MB for A4@72DPI) is saved to a temp file and the in-memory cache
                is cleared.  This bounds peak image memory to O(batch_size) instead of
                O(total_pages), preventing the progressive GC pressure that causes
                later pages to parse more slowly.
                """
                from docling.pipeline.standard_pdf_pipeline import ThreadedItem

                if not isinstance(item, ThreadedItem):
                    super()._release_page_resources(item)
                    return

                page = item.payload
                if page is None:
                    return

                images_dir = owner._page_images_dir
                if images_dir is not None:
                    if page.image is not None:
                        img_path = images_dir / f"page_{page.page_no}.png"
                        img_path.parent.mkdir(parents=True, exist_ok=True)
                        page.image.save(str(img_path))
                    page._image_cache = {}
                elif not self.keep_images:
                    page._image_cache = {}

                if not self.keep_backend and page._backend is not None:
                    page._backend.unload()
                    page._backend = None
                if not self.pipeline_options.generate_parsed_pages:
                    page.parsed_page = None

            def _assemble_document(self, conv_res):
                """Override: reload page images from disk for picture cropping.

                Loads images on-demand before the parent assembles the document
                (which crops picture/table elements from page images), then
                immediately frees them and deletes the temp directory.
                """
                from PIL import Image as PILImage

                images_dir = owner._page_images_dir
                if images_dir is not None:
                    for page in conv_res.pages:
                        img_path = images_dir / f"page_{page.page_no}.png"
                        if img_path.exists():
                            page._image_cache[page._default_image_scale] = PILImage.open(img_path)

                result = super()._assemble_document(conv_res)

                # Immediately free all page images and temp files
                for page in conv_res.pages:
                    page._image_cache = {}
                if images_dir is not None and images_dir.exists():
                    shutil.rmtree(images_dir, ignore_errors=True)

                return result

            def _create_run_ctx(self):
                ctx = super()._create_run_ctx()
                # Read callback dynamically so it can be changed between calls
                callback = owner._callback
                if callback is None:
                    logger.warning(
                        "[DOCLING] _create_run_ctx: no callback set, progress hooks disabled"
                    )
                    return ctx
                hooked_stages = []
                for stage in ctx.stages:
                    # Skip ocr stage (no-op when do_ocr=False)
                    if stage.name == "ocr":
                        continue
                    original = stage._postprocess
                    stage_name = stage.name

                    def _make_wrapper(orig, name):
                        def _wrapper(item):
                            if orig:
                                orig(item)
                            if not item.is_failed:
                                callback(name, item.page_no)
                        return _wrapper

                    stage._postprocess = _make_wrapper(original, stage_name)
                    hooked_stages.append(stage_name)
                logger.info(
                    f"[DOCLING] _create_run_ctx: hooked {len(hooked_stages)} "
                    f"stages for progress: {hooked_stages}"
                )
                return ctx

        cls._pipeline_cls = _ProgressPipeline
        return _ProgressPipeline


class DoclingConverter:
    """Docling-based PDF converter with model caching.

    Parses PDF documents using Docling, producing:
    1. Markdown with image file links (not base64, not OCR)
    2. Images saved to a subdirectory
    3. DoclingDocument JSON for structured tool access

    The DocumentConverter (and its heavy pipeline models) is cached at the class
    level so that it is reused across calls within the same process. In the
    Celery prefork model each worker process handles one task at a time, so the
    cache is safe without additional locking.
    """

    _cached_converter: "DocumentConverter | None" = None

    def __init__(self, timeout: int = 600):
        self.timeout = timeout

    @classmethod
    def _get_models_dir(cls) -> Path:
        """Return the local models directory."""
        return Path(__file__).parent.parent.parent / "models"

    @classmethod
    def _get_converter(cls):
        """Return a cached DocumentConverter, creating it on first call."""
        if cls._cached_converter is not None:
            return cls._cached_converter

        docling_path = Path(__file__).parent.parent.parent / "third_party" / "docling"
        if str(docling_path) not in sys.path:
            sys.path.insert(0, str(docling_path))

        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        models_dir = cls._get_models_dir()
        if not (models_dir / "docling-project--docling-layout-heron").exists():
            logger.warning(
                f"Local models not found at {models_dir}, "
                "Docling will download models from HuggingFace"
            )
            models_dir = None

        pipeline_options = PdfPipelineOptions(
            artifacts_path=models_dir,
            do_ocr=False,
            do_table_structure=True,
            generate_picture_images=True,
            generate_page_images=False,
            images_scale=1.0,
            accelerator_options=AcceleratorOptions(num_threads=2),
            document_timeout=None,
        )

        pipeline_cls = ProgressReportingPdfPipeline.get_pipeline_class()

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=pipeline_options,
                    pipeline_cls=pipeline_cls,
                ),
            },
        )
        cls._cached_converter = converter
        logger.info("DoclingConverter: created and cached DocumentConverter instance")
        return converter

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

    def _create_page_images_dir(self) -> Path:
        """Create a temp directory for offloading page images during conversion."""
        tmpdir = Path(tempfile.mkdtemp(prefix="docling_pages_"))
        ProgressReportingPdfPipeline.set_page_images_dir(tmpdir)
        return tmpdir

    @staticmethod
    def _cleanup_page_images_dir(tmpdir: Path | None):
        """Safely clean up the page images temp directory."""
        ProgressReportingPdfPipeline.set_page_images_dir(None)
        if tmpdir is not None and tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _convert_pdf(
        self,
        file_path: Path,
        images_dir: Path,
        docling_json_path: Path,
    ) -> ConversionResult:
        import time as time_module
        from docling_core.types.doc import ImageRefMode

        converter = self._get_converter()

        md_path = file_path.parent / f"{file_path.stem}_parsed.md"

        # Fix 1: offload page images to disk to bound memory during pipeline
        tmpdir = self._create_page_images_dir()
        logger.info(
            f"[DOCLING] Calling DocumentConverter.convert() on {file_path.name} "
            f"({file_path.stat().st_size / (1024*1024):.2f}MB)..."
        )
        convert_start = time_module.time()
        try:
            result = converter.convert(str(file_path))
        finally:
            self._cleanup_page_images_dir(tmpdir)
        convert_elapsed = time_module.time() - convert_start

        logger.info(
            f"[DOCLING] Pipeline finished: status={result.status.name}, "
            f"elapsed={convert_elapsed:.1f}s"
        )

        if result.status.name == "FAILURE":
            errors = "; ".join(str(e) for e in result.errors) if result.errors else "unknown"
            raise DoclingConversionError(f"Docling conversion failed: {errors}")

        # Log any warnings from Docling (e.g. timeout warnings)
        if result.errors:
            for err in result.errors:
                logger.warning(f"[DOCLING] Pipeline error/warning: {err}")

        page_count = len(result.pages) if result.pages else None

        md_path.parent.mkdir(parents=True, exist_ok=True)
        result.document.save_as_markdown(
            filename=md_path,
            artifacts_dir=images_dir,
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
            f"[DOCLING] Output: {len(markdown_content)} chars, "
            f"{len(images)} images, {page_count} pages"
        )

        # Fix 2: explicit cleanup of Docling ConversionResult to free memory
        for p in result.pages:
            p._image_cache = {}
        del result
        gc.collect()

        return ConversionResult(
            markdown_content=markdown_content,
            images=images,
            page_count=page_count,
        )
