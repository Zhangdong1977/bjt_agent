"""Tests for provider-safe OCR image normalization."""

from pathlib import Path

import pytest
from PIL import Image, UnidentifiedImageError, features

from backend.services.ocr_image_normalizer import (
    OcrImageNormalizationError,
    normalize_image_for_ocr,
)


def _require_jpeg2000() -> None:
    if not features.check("jpg_2000"):
        pytest.skip("Pillow was built without JPEG 2000 support")


def _save_jpeg2000(path: Path, *, size=(640, 360)) -> Path:
    _require_jpeg2000()
    Image.new("RGB", size, "white").save(path, format="JPEG2000")
    return path


def test_png_within_limits_is_not_reencoded(tmp_path: Path):
    source = tmp_path / "source.png"
    Image.new("RGB", (320, 180), "white").save(source)

    result = normalize_image_for_ocr(source, cache_dir=tmp_path / "cache")

    assert result.path == source
    assert result.converted is False
    assert result.source_format == result.output_format == "PNG"
    assert result.source_sha256 == result.normalized_sha256


def test_jpx_is_converted_and_cached_without_touching_source(tmp_path: Path):
    source = _save_jpeg2000(tmp_path / "certificate.jpx")
    original = source.read_bytes()
    cache_dir = tmp_path / "cache"

    first = normalize_image_for_ocr(source, cache_dir=cache_dir)
    second = normalize_image_for_ocr(source, cache_dir=cache_dir)

    assert source.read_bytes() == original
    assert first.source_format == "JPEG2000"
    assert first.output_format in {"PNG", "JPEG"}
    assert first.path != source
    assert first.path.is_file()
    assert first.output_size_bytes <= 4 * 1024 * 1024
    assert second.path == first.path
    assert second.cache_hit is True
    with Image.open(first.path) as converted:
        assert converted.format in {"PNG", "JPEG"}
        assert converted.size == (640, 360)


def test_content_detection_converts_jpeg2000_with_jpg_suffix(tmp_path: Path):
    source = _save_jpeg2000(tmp_path / "mislabelled.jpg")

    result = normalize_image_for_ocr(source, cache_dir=tmp_path / "cache")

    assert result.source_format == "JPEG2000"
    assert result.converted is True
    assert result.output_format in {"PNG", "JPEG"}


def test_jpeg2000_uses_pymupdf_when_pillow_decoder_is_unavailable(
    monkeypatch, tmp_path: Path
):
    source = _save_jpeg2000(tmp_path / "fallback.jpx", size=(80, 60))

    def unavailable_decoder(*args, **kwargs):
        raise UnidentifiedImageError("JPEG 2000 plugin unavailable")

    monkeypatch.setattr(Image, "open", unavailable_decoder)
    result = normalize_image_for_ocr(source, cache_dir=tmp_path / "cache")

    assert result.source_format == "JPEG2000"
    assert result.output_format in {"PNG", "JPEG"}
    assert result.width == 80
    assert result.height == 60


def test_transparency_is_flattened_to_white(tmp_path: Path):
    source = tmp_path / "transparent.tiff"
    image = Image.new("RGBA", (40, 40), (255, 0, 0, 0))
    image.putpixel((20, 20), (0, 0, 0, 255))
    image.save(source)

    result = normalize_image_for_ocr(source, cache_dir=tmp_path / "cache")

    with Image.open(result.path) as converted:
        rgb = converted.convert("RGB")
        assert rgb.getpixel((0, 0)) == (255, 255, 255)
        assert max(rgb.getpixel((20, 20))) < 20


def test_corrupt_image_has_explicit_normalization_error(tmp_path: Path):
    source = tmp_path / "broken.jpx"
    source.write_bytes(b"not-an-image")

    with pytest.raises(OcrImageNormalizationError, match="无法解码图片格式"):
        normalize_image_for_ocr(source, cache_dir=tmp_path / "cache")


def test_tiny_image_is_upscaled_to_min_dimension(tmp_path: Path):
    """最短边低于百度下限（50px）的有效图片应等比放大到下限，而非原样放行。

    回归保护：修复前 5x5 这类极小有效图会被直接透传给百度，触发 216202 image size error。
    """
    source = tmp_path / "tiny.png"
    Image.new("RGB", (5, 5), "white").save(source, format="PNG")

    result = normalize_image_for_ocr(source, cache_dir=tmp_path / "cache")

    assert result.converted is True
    assert min(result.width, result.height) >= 50
    assert result.width == result.height == 50


def test_thin_image_satisfies_both_dimension_bounds(tmp_path: Path):
    """瘦长图按统一 scale 同时满足长边上限和短边下限，不能分两步 resize 致长边暴涨。"""
    source = tmp_path / "thin.jpg"
    Image.new("RGB", (1, 5000), "white").save(source, format="JPEG", quality=85)

    result = normalize_image_for_ocr(source, cache_dir=tmp_path / "cache")

    assert result.converted is True
    assert max(result.width, result.height) <= 4096
    assert min(result.width, result.height) >= 50

