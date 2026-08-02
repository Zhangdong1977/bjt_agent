"""Normalize extracted document images before sending them to OCR providers."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

NORMALIZATION_POLICY_VERSION = "v1"
DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_DIMENSION = 4096
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_PIXELS = 60_000_000
_DIRECT_FORMATS = {"JPEG", "PNG"}


class OcrImageNormalizationError(ValueError):
    """Raised when an image cannot be safely decoded for OCR."""


@dataclass(frozen=True, slots=True)
class NormalizedOcrImage:
    source_path: Path
    path: Path
    source_sha256: str
    normalized_sha256: str
    source_format: str
    output_format: str
    mime_type: str
    converted: bool
    cache_hit: bool
    original_size_bytes: int
    output_size_bytes: int
    width: int
    height: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_cache_dir() -> Path:
    from backend.config import get_settings

    return get_settings().workspace_path / ".ocr_image_cache"


def _mime_type(image_format: str) -> str:
    return "image/jpeg" if image_format == "JPEG" else "image/png"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _cached_result(
    *,
    source_path: Path,
    cache_path: Path,
    source_sha256: str,
    source_format: str,
    original_size_bytes: int,
) -> NormalizedOcrImage:
    from PIL import Image

    with Image.open(cache_path) as image:
        width, height = image.size
        output_format = (image.format or "").upper()
    if output_format not in _DIRECT_FORMATS:
        raise OcrImageNormalizationError(
            f"OCR 图片缓存格式异常: {output_format or 'unknown'}"
        )
    return NormalizedOcrImage(
        source_path=source_path,
        path=cache_path,
        source_sha256=source_sha256,
        normalized_sha256=_sha256_file(cache_path),
        source_format=source_format,
        output_format=output_format,
        mime_type=_mime_type(output_format),
        converted=True,
        cache_hit=True,
        original_size_bytes=original_size_bytes,
        output_size_bytes=cache_path.stat().st_size,
        width=int(width),
        height=int(height),
    )


def _prepare_image(image, *, max_dimension: int):
    from PIL import Image, ImageOps

    frame_count = int(getattr(image, "n_frames", 1) or 1)
    if frame_count > 1:
        logger.warning(
            "OCR image has %d frames; only the first frame is used", frame_count
        )
        image.seek(0)

    prepared = ImageOps.exif_transpose(image).copy()
    prepared.load()
    if prepared.width * prepared.height > MAX_SOURCE_PIXELS:
        raise OcrImageNormalizationError(
            f"图片像素过大: {prepared.width}x{prepared.height}"
        )

    if max(prepared.size) > max_dimension:
        scale = max_dimension / max(prepared.size)
        prepared = prepared.resize(
            (
                max(1, round(prepared.width * scale)),
                max(1, round(prepared.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )

    has_alpha = "A" in prepared.getbands() or (
        prepared.mode == "P" and "transparency" in prepared.info
    )
    if has_alpha:
        rgba = prepared.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        prepared = background
    elif prepared.mode not in {"RGB", "L"}:
        prepared = prepared.convert("RGB")
    return prepared


def _looks_like_jpeg2000(path: Path) -> bool:
    with path.open("rb") as handle:
        header = handle.read(32)
    return header.startswith(b"\x00\x00\x00\x0cjP  \r\n\x87\n") or header.startswith(
        b"\xff\x4f\xff\x51"
    )


def _decode_jpeg2000_with_pymupdf(path: Path):
    """Decode JPEG 2000 when the installed Pillow lacks OpenJPEG support."""

    import fitz
    from PIL import Image

    pixmap = fitz.Pixmap(str(path))
    if pixmap.colorspace and pixmap.colorspace.n not in {1, 3}:
        pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
    mode_by_components = {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}
    mode = mode_by_components.get(pixmap.n)
    if mode is None:
        raise OcrImageNormalizationError(
            f"PyMuPDF 解码后的图片通道数不受支持: {pixmap.n}"
        )
    return Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples)


def _encode_image(
    image, *, max_output_bytes: int, prefer_jpeg: bool
) -> tuple[bytes, str, tuple[int, int]]:
    from io import BytesIO

    from PIL import Image

    def encode_png(candidate) -> bytes:
        buffer = BytesIO()
        candidate.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def encode_jpeg(candidate, quality: int) -> bytes:
        buffer = BytesIO()
        candidate.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=False,
        )
        return buffer.getvalue()

    if not prefer_jpeg:
        png_data = encode_png(image)
        if len(png_data) <= max_output_bytes:
            return png_data, "PNG", image.size

    for quality in (92, 85, 75):
        jpeg_data = encode_jpeg(image, quality)
        if len(jpeg_data) <= max_output_bytes:
            return jpeg_data, "JPEG", image.size

    candidate = image
    last_data = jpeg_data
    for _ in range(8):
        ratio = math.sqrt(max_output_bytes / max(1, len(last_data))) * 0.95
        ratio = min(0.90, max(0.50, ratio))
        new_size = (
            max(1, round(candidate.width * ratio)),
            max(1, round(candidate.height * ratio)),
        )
        if new_size == candidate.size:
            break
        candidate = candidate.resize(new_size, Image.Resampling.LANCZOS)
        last_data = encode_jpeg(candidate, 80)
        if len(last_data) <= max_output_bytes:
            return last_data, "JPEG", candidate.size

    raise OcrImageNormalizationError(
        f"图片转换后仍超过 OCR 大小限制: {len(last_data) / (1024 * 1024):.1f}MB"
    )


def normalize_image_for_ocr(
    source_path: Path | str,
    *,
    cache_dir: Path | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    force_reencode: bool = False,
    prefer_jpeg: bool = False,
    source_sha256: str | None = None,
) -> NormalizedOcrImage:
    """Return a provider-safe JPEG/PNG while preserving the extracted source file."""

    from PIL import Image, UnidentifiedImageError

    path = Path(source_path)
    if not path.is_file():
        raise OcrImageNormalizationError(f"图片文件不存在: {path}")
    original_size = path.stat().st_size
    if original_size > MAX_SOURCE_BYTES:
        raise OcrImageNormalizationError(
            f"图片源文件过大: {original_size / (1024 * 1024):.1f}MB"
        )
    if max_output_bytes <= 0 or max_dimension <= 0:
        raise ValueError("OCR 图片大小限制必须为正数")

    try:
        with Image.open(path) as image:
            source_format = (image.format or "UNKNOWN").upper()
            width, height = image.size
            if width * height > MAX_SOURCE_PIXELS:
                raise OcrImageNormalizationError(f"图片像素过大: {width}x{height}")
            orientation = int(image.getexif().get(274, 1) or 1)
            frame_count = int(getattr(image, "n_frames", 1) or 1)
            png_has_alpha = "A" in image.getbands() or (
                image.mode == "P" and "transparency" in image.info
            )
            direct_mode = (
                source_format == "JPEG" and image.mode in {"RGB", "L"}
            ) or (
                source_format == "PNG"
                and image.mode in {"RGB", "L", "P"}
                and not png_has_alpha
            )
            can_use_directly = (
                not force_reencode
                and source_format in _DIRECT_FORMATS
                and direct_mode
                and orientation == 1
                and frame_count == 1
                and original_size <= max_output_bytes
                and max(width, height) <= max_dimension
            )
            digest = source_sha256 or _sha256_file(path)
            if can_use_directly:
                # Force complete decoding so truncated images fail locally instead of
                # surfacing as an opaque provider-side format error.
                image.load()
                return NormalizedOcrImage(
                    source_path=path,
                    path=path,
                    source_sha256=digest,
                    normalized_sha256=digest,
                    source_format=source_format,
                    output_format=source_format,
                    mime_type=_mime_type(source_format),
                    converted=False,
                    cache_hit=False,
                    original_size_bytes=original_size,
                    output_size_bytes=original_size,
                    width=int(width),
                    height=int(height),
                )
            prepared = _prepare_image(image, max_dimension=max_dimension)
    except OcrImageNormalizationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        if not _looks_like_jpeg2000(path):
            raise OcrImageNormalizationError(
                f"无法解码图片格式: {path.name}: {exc}"
            ) from exc
        try:
            fallback_image = _decode_jpeg2000_with_pymupdf(path)
            source_format = "JPEG2000"
            prepared = _prepare_image(fallback_image, max_dimension=max_dimension)
            logger.info("Decoded JPEG 2000 with PyMuPDF fallback: %s", path.name)
        except Exception as fallback_exc:
            raise OcrImageNormalizationError(
                f"无法解码 JPEG 2000 图片: {path.name}: {fallback_exc}"
            ) from fallback_exc

    digest = source_sha256 or _sha256_file(path)
    variant = "jpeg" if prefer_jpeg else "auto"
    policy = f"{NORMALIZATION_POLICY_VERSION}-{max_output_bytes}-{max_dimension}-{variant}"
    cache_root = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
    base_path = cache_root / policy / digest[:2] / digest
    for suffix in (".png", ".jpg"):
        cached_path = base_path.with_suffix(suffix)
        if cached_path.is_file() and 0 < cached_path.stat().st_size <= max_output_bytes:
            try:
                return _cached_result(
                    source_path=path,
                    cache_path=cached_path,
                    source_sha256=digest,
                    source_format=source_format,
                    original_size_bytes=original_size,
                )
            except Exception:
                logger.warning("Invalid OCR image cache ignored: %s", cached_path, exc_info=True)

    data, output_format, output_size = _encode_image(
        prepared,
        max_output_bytes=max_output_bytes,
        prefer_jpeg=prefer_jpeg,
    )
    output_path = base_path.with_suffix(".jpg" if output_format == "JPEG" else ".png")
    _atomic_write(output_path, data)
    logger.info(
        "Normalized OCR image %s: %s -> %s, %dKB -> %dKB",
        path.name,
        source_format,
        output_format,
        original_size // 1024,
        len(data) // 1024,
    )
    return NormalizedOcrImage(
        source_path=path,
        path=output_path,
        source_sha256=digest,
        normalized_sha256=hashlib.sha256(data).hexdigest(),
        source_format=source_format,
        output_format=output_format,
        mime_type=_mime_type(output_format),
        converted=True,
        cache_hit=False,
        original_size_bytes=original_size,
        output_size_bytes=len(data),
        width=int(output_size[0]),
        height=int(output_size[1]),
    )


__all__ = [
    "DEFAULT_MAX_DIMENSION",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "NormalizedOcrImage",
    "OcrImageNormalizationError",
    "normalize_image_for_ocr",
]
