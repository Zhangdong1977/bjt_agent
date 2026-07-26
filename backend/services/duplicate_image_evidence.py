"""Selective image hashing, OCR and scanned-page evidence for duplicate checks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

OcrCallable = Callable[[Path], Awaitable[tuple[str, float | None, str]]]
VisionCallable = Callable[[Path], Awaitable[str]]


def sha256_image(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def perceptual_dhash(path: Path) -> str | None:
    """Return a deterministic 64-bit difference hash using Pillow only."""

    try:
        from PIL import Image

        with Image.open(path) as image:
            grayscale = image.convert("L").resize((9, 8))
            pixels = list(
                grayscale.get_flattened_data()
                if hasattr(grayscale, "get_flattened_data")
                else grayscale.getdata()
            )
        bits = 0
        for row in range(8):
            for column in range(8):
                left = pixels[row * 9 + column]
                right = pixels[row * 9 + column + 1]
                bits = (bits << 1) | int(left > right)
        return f"{bits:016x}"
    except Exception:
        logger.warning("Unable to calculate perceptual hash: %s", path, exc_info=True)
        return None


def perceptual_similarity(left: str | None, right: str | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    try:
        distance = (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return 0.0
    return max(0.0, 1.0 - distance / (len(left) * 4))


def image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None


@dataclass(slots=True)
class ImageEvidence:
    image_path: str
    image_sha256: str
    perceptual_hash: str | None
    width: int | None
    height: int | None
    page_number: int | None = None
    bbox: dict[str, float] | None = None
    ocr_text: str = ""
    ocr_confidence: float | None = None
    ocr_provider: str | None = None
    ocr_error: str | None = None
    vision_description: str | None = None
    cache_hit: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class PdfPageClassification:
    page_number: int
    kind: str
    extracted_text_length: int
    image_count: int


def classify_pdf_pages(
    file_path: Path,
    *,
    text_threshold: int = 30,
) -> list[PdfPageClassification]:
    """Classify every page as text or scan without failing the whole PDF."""

    import fitz

    result: list[PdfPageClassification] = []
    document = fitz.open(str(file_path))
    try:
        for index, page in enumerate(document):
            text_length = len((page.get_text() or "").strip())
            image_count = len(page.get_images(full=True))
            result.append(
                PdfPageClassification(
                    page_number=index + 1,
                    kind="text" if text_length >= max(1, int(text_threshold)) else "scan",
                    extracted_text_length=text_length,
                    image_count=image_count,
                )
            )
    finally:
        document.close()
    return result


def render_pdf_pages(
    file_path: Path,
    pages: list[int],
    output_dir: Path,
    *,
    dpi: int = 144,
) -> dict[int, Path]:
    import fitz

    output_dir.mkdir(parents=True, exist_ok=True)
    requested = {int(page) for page in pages if int(page) > 0}
    rendered: dict[int, Path] = {}
    document = fitz.open(str(file_path))
    try:
        scale = dpi / 72.0
        matrix = fitz.Matrix(scale, scale)
        for page_number in sorted(requested):
            if page_number > len(document):
                continue
            page = document[page_number - 1]
            path = output_dir / f"page_{page_number}_scan.png"
            page.get_pixmap(matrix=matrix, alpha=False).save(str(path))
            rendered[page_number] = path
    finally:
        document.close()
    return rendered


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


class SelectiveImageEvidenceService:
    """Hash all images, OCR only selected images, and cache by image SHA-256."""

    def __init__(
        self,
        *,
        cache_dir: Path,
        ocr_enabled: bool = True,
        remote_ocr_enabled: bool = False,
        vision_enabled: bool = False,
        max_ocr_images: int = 24,
        max_remote_calls: int = 4,
        max_vision_calls: int = 2,
        min_local_confidence: float = 0.72,
        local_ocr: OcrCallable | None = None,
        remote_ocr: OcrCallable | None = None,
        vision: VisionCallable | None = None,
    ):
        self.cache_dir = cache_dir
        self.ocr_enabled = ocr_enabled
        self.remote_ocr_enabled = remote_ocr_enabled
        self.vision_enabled = vision_enabled
        self.max_ocr_images = max(0, int(max_ocr_images))
        self.max_remote_calls = max(0, int(max_remote_calls))
        self.max_vision_calls = max(0, int(max_vision_calls))
        self.min_local_confidence = min(1.0, max(0.0, float(min_local_confidence)))
        self.local_ocr = local_ocr or self._rapidocr
        self.remote_ocr = remote_ocr or self._baidu_ocr
        self.vision = vision or (self._configured_vision if vision_enabled else None)
        self.ocr_attempts = 0
        self.remote_calls = 0
        self.vision_calls = 0

    def _cache_path(self, digest: str) -> Path:
        return self.cache_dir / digest[:2] / f"{digest}.json"

    @staticmethod
    def should_ocr(path: Path, *, force: bool, width: int | None, height: int | None) -> bool:
        if force:
            return True
        if width is None or height is None:
            return False
        # Skip tiny icons/logos by default; certificates, screenshots and page
        # renders are large enough to pass this cheap local gate.
        return width >= 320 and height >= 160 and path.stat().st_size >= 8 * 1024

    async def analyze(
        self,
        path: Path,
        *,
        force_ocr: bool = False,
        page_number: int | None = None,
        bbox: dict[str, float] | None = None,
    ) -> ImageEvidence:
        digest = await asyncio.to_thread(sha256_image, path)
        cache_path = self._cache_path(digest)
        if cache_path.is_file():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                payload["image_path"] = str(path)
                payload["page_number"] = page_number or payload.get("page_number")
                payload["bbox"] = bbox or payload.get("bbox")
                payload["cache_hit"] = True
                return ImageEvidence(**payload)
            except Exception:
                logger.warning("Invalid OCR cache ignored: %s", cache_path, exc_info=True)

        width, height = await asyncio.to_thread(image_dimensions, path)
        perceptual = await asyncio.to_thread(perceptual_dhash, path)
        evidence = ImageEvidence(
            image_path=str(path),
            image_sha256=digest,
            perceptual_hash=perceptual,
            width=width,
            height=height,
            page_number=page_number,
            bbox=bbox,
        )
        selected = self.should_ocr(
            path,
            force=force_ocr,
            width=width,
            height=height,
        )
        if not self.ocr_enabled or not selected:
            if force_ocr and not self.ocr_enabled:
                evidence.warnings.append("ocr_disabled")
            self._write_cache(evidence)
            return evidence
        if self.ocr_attempts >= self.max_ocr_images:
            evidence.ocr_error = "ocr_budget_exhausted"
            evidence.warnings.append("ocr_budget_exhausted")
            self._write_cache(evidence)
            return evidence

        self.ocr_attempts += 1
        try:
            text, confidence, provider = await self.local_ocr(path)
            evidence.ocr_text = text.strip()
            evidence.ocr_confidence = confidence
            evidence.ocr_provider = provider
        except Exception as exc:
            evidence.ocr_error = f"local_ocr_failed:{type(exc).__name__}"
            evidence.warnings.append(evidence.ocr_error)

        low_confidence = (
            not evidence.ocr_text
            or evidence.ocr_confidence is None
            or evidence.ocr_confidence < self.min_local_confidence
        )
        if self.remote_ocr_enabled and low_confidence:
            if self.remote_calls >= self.max_remote_calls:
                evidence.warnings.append("remote_ocr_budget_exhausted")
            else:
                self.remote_calls += 1
                try:
                    text, confidence, provider = await self.remote_ocr(path)
                    if text.strip():
                        evidence.ocr_text = text.strip()
                        evidence.ocr_confidence = confidence
                        evidence.ocr_provider = provider
                        evidence.ocr_error = None
                except Exception as exc:
                    evidence.ocr_error = f"remote_ocr_failed:{type(exc).__name__}"
                    evidence.warnings.append(evidence.ocr_error)

        if (
            self.vision_enabled
            and not evidence.ocr_text
            and self.vision is not None
        ):
            if self.vision_calls >= self.max_vision_calls:
                evidence.warnings.append("vision_budget_exhausted")
            else:
                self.vision_calls += 1
                try:
                    evidence.vision_description = (await self.vision(path)).strip() or None
                except Exception as exc:
                    evidence.warnings.append(f"vision_failed:{type(exc).__name__}")

        if force_ocr and not evidence.ocr_text:
            evidence.warnings.append("scan_page_ocr_unresolved")
        self._write_cache(evidence)
        return evidence

    def _write_cache(self, evidence: ImageEvidence) -> None:
        payload = evidence.to_dict()
        payload["image_path"] = Path(evidence.image_path).name
        payload["cache_hit"] = False
        try:
            _atomic_json(self._cache_path(evidence.image_sha256), payload)
        except Exception:
            logger.warning("Unable to write image evidence cache", exc_info=True)

    @staticmethod
    async def _rapidocr(path: Path) -> tuple[str, float | None, str]:
        started = time.perf_counter()
        try:
            from rapidocr import RapidOCR
            from backend.config import get_settings

            engine = RapidOCR(
                params={"Global.model_root_dir": str(get_settings().ocr_model_dir)}
            )
            output = await asyncio.to_thread(engine, str(path))
            texts = list(output.txts or [])
            scores = [float(score) for score in (output.scores or [])]
            confidence = sum(scores) / len(scores) if scores else None
            from backend.services.usage_recorder import record_ocr_usage

            record_ocr_usage(
                provider="rapidocr",
                endpoint="local",
                status="success",
                latency_ms=int((time.perf_counter() - started) * 1000),
                words_result_num=len(texts),
                image_size_bytes=path.stat().st_size,
            )
            return "\n".join(texts), confidence, "rapidocr"
        except Exception as exc:
            try:
                from backend.services.usage_recorder import record_ocr_usage

                record_ocr_usage(
                    provider="rapidocr",
                    endpoint="local",
                    status="error",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    image_size_bytes=path.stat().st_size,
                    error_message=str(exc),
                )
            except Exception:
                pass
            raise

    @staticmethod
    async def _baidu_ocr(path: Path) -> tuple[str, float | None, str]:
        from backend.agent.tools.baidu_ocr import BaiduOcrTool

        result = await BaiduOcrTool().execute(
            prompt="提取图片中用于标书查重的编号、型号、联系人、单位和正文",
            image_source=str(path),
        )
        if not result.success:
            raise RuntimeError(result.error or "Baidu OCR failed")
        return result.content or "", 0.80 if result.content else None, "baidu_ocr"

    @staticmethod
    async def _configured_vision(path: Path) -> str:
        from backend.config import get_settings
        from backend.services.usage_recorder import record_vision_usage

        settings = get_settings()
        started = time.perf_counter()
        provider = settings.image_understanding_provider
        try:
            if provider != "volcengine" and settings.llm_provider != "volcengine":
                raise RuntimeError(
                    "duplicate VLM currently requires image_understanding_provider=volcengine"
                )
            from backend.agent.tools.volcengine_vision import VolcengineVisionTool

            result = await VolcengineVisionTool().execute(
                prompt=(
                    "仅描述用于标书查重的图表结构、证书版式、异常标记和可辨识编号；"
                    "不要推断法律结论。"
                ),
                image_source=str(path),
            )
            if not result.success:
                raise RuntimeError(result.error or "vision failed")
            record_vision_usage(
                provider="volcengine_vision",
                model=settings.volcengine_model,
                status="success",
                latency_ms=int((time.perf_counter() - started) * 1000),
                image_size_bytes=path.stat().st_size,
            )
            return result.content or ""
        except Exception as exc:
            record_vision_usage(
                provider=f"{provider}_vision",
                model=(settings.volcengine_model if provider == "volcengine" else None),
                status="error",
                latency_ms=int((time.perf_counter() - started) * 1000),
                image_size_bytes=path.stat().st_size,
                error_message=str(exc),
            )
            raise


__all__ = [
    "ImageEvidence",
    "PdfPageClassification",
    "SelectiveImageEvidenceService",
    "classify_pdf_pages",
    "image_dimensions",
    "perceptual_dhash",
    "perceptual_similarity",
    "render_pdf_pages",
    "sha256_image",
]
