"""S2-2A selective OCR, image hashing and scanned-page tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services.document_artifacts import build_document_artifacts, load_evidence_blocks
from backend.services.duplicate_candidates import DocumentDescriptor, DuplicateCandidateService
from backend.services.duplicate_image_evidence import (
    SelectiveImageEvidenceService,
    classify_pdf_pages,
    perceptual_dhash,
    perceptual_similarity,
    render_pdf_pages,
)


def _image(path: Path, size=(640, 360), text_mark=False) -> Path:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, "white")
    if text_mark:
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, size[0] - 40, size[1] - 40), outline="black", width=8)
        draw.text((80, 100), "ZX-900 48 PORTS", fill="black")
    image.save(path)
    return path


def test_perceptual_hash_survives_resize_and_compression(tmp_path: Path):
    original = _image(tmp_path / "original.png", text_mark=True)
    from PIL import Image

    with Image.open(original) as image:
        image.resize((320, 180)).save(tmp_path / "resized.jpg", quality=70)
    left = perceptual_dhash(original)
    right = perceptual_dhash(tmp_path / "resized.jpg")
    assert left and right
    assert perceptual_similarity(left, right) >= 0.90


@pytest.mark.asyncio
async def test_ocr_cache_prevents_duplicate_provider_calls(tmp_path: Path):
    calls = 0

    async def local_ocr(_path):
        nonlocal calls
        calls += 1
        return "证书编号 ZX-900", 0.96, "fake-local"

    image = _image(tmp_path / "certificate.png", text_mark=True)
    service = SelectiveImageEvidenceService(
        cache_dir=tmp_path / "cache",
        local_ocr=local_ocr,
        max_ocr_images=1,
    )
    first = await service.analyze(image, force_ocr=True)
    second = await service.analyze(image, force_ocr=True)
    assert first.ocr_text == second.ocr_text == "证书编号 ZX-900"
    assert second.cache_hit is True
    assert calls == 1


@pytest.mark.asyncio
async def test_remote_ocr_is_bounded_and_only_fallbacks_low_confidence(tmp_path: Path):
    remote_calls = 0

    async def weak_local(_path):
        return "模糊", 0.20, "fake-local"

    async def remote(_path):
        nonlocal remote_calls
        remote_calls += 1
        return "清晰编号 AB-123", 0.88, "fake-remote"

    first_image = _image(tmp_path / "one.png", text_mark=True)
    second_image = _image(tmp_path / "two.png", size=(700, 360), text_mark=True)
    service = SelectiveImageEvidenceService(
        cache_dir=tmp_path / "cache",
        local_ocr=weak_local,
        remote_ocr=remote,
        remote_ocr_enabled=True,
        max_remote_calls=1,
        max_ocr_images=2,
    )
    first = await service.analyze(first_image, force_ocr=True)
    second = await service.analyze(second_image, force_ocr=True)
    assert first.ocr_text == "清晰编号 AB-123"
    assert remote_calls == 1
    assert "remote_ocr_budget_exhausted" in second.warnings


def test_artifact_contains_image_and_linked_ocr_block(tmp_path: Path):
    source = tmp_path / "source.docx"
    source.write_bytes(b"source")
    images = tmp_path / "source_images"
    images.mkdir()
    image = _image(images / "page_2_scan.png", text_mark=True)
    markdown = tmp_path / "source_parsed.md"
    markdown.write_text(
        "# 扫描证书\n\n![扫描页](source_images/page_2_scan.png)",
        encoding="utf-8",
    )
    result = build_document_artifacts(
        document_id="doc-image",
        document_role="duplicate_left",
        original_filename=source.name,
        source_path=source,
        markdown_path=markdown,
        images_dir=images,
        parsed_data={
            "text": markdown.read_text(encoding="utf-8"),
            "images": [{"filename": image.name}],
            "image_evidence": {
                image.name: {
                    "image_sha256": "a" * 64,
                    "perceptual_hash": "0123456789abcdef",
                    "width": 640,
                    "height": 360,
                    "page_number": 2,
                    "ocr_text": "证书编号 ZX-900",
                    "ocr_confidence": 0.93,
                    "ocr_provider": "fake-local",
                }
            },
            "page_count": 2,
            "parsed_page_count": 2,
            "scanned_page_count": 1,
            "ocr_page_count": 1,
        },
    )
    blocks = load_evidence_blocks(result["evidence_blocks_path"])
    image_block = next(block for block in blocks if block.content_type == "image")
    ocr_block = next(block for block in blocks if block.content_type == "image_ocr")
    assert image_block.perceptual_hash == "0123456789abcdef"
    assert image_block.page_number == 2
    assert ocr_block.parent_block_id == image_block.block_id
    assert ocr_block.raw_text == "证书编号 ZX-900"
    assert result["coverage"].ocr_page_count == 1


def test_pdf_page_classification_and_rendering_is_page_scoped(tmp_path: Path):
    import fitz

    pdf = tmp_path / "mixed.pdf"
    document = fitz.open()
    first = document.new_page()
    first.insert_text((72, 72), "This page has extractable technical text " * 3)
    document.new_page()  # scan/blank surrogate
    document.save(str(pdf))
    document.close()

    pages = classify_pdf_pages(pdf, text_threshold=30)
    assert [page.kind for page in pages] == ["text", "scan"]
    rendered = render_pdf_pages(pdf, [2], tmp_path / "pages")
    assert list(rendered) == [2]
    assert rendered[2].is_file()


@pytest.mark.asyncio
async def test_exact_image_hash_enters_candidate_channel(tmp_path: Path):
    shared = _image(tmp_path / "shared.png", text_mark=True)

    def artifact(document_id: str) -> dict:
        source = tmp_path / f"{document_id}.docx"
        source.write_bytes(document_id.encode())
        image_dir = tmp_path / f"{document_id}_images"
        image_dir.mkdir()
        target = image_dir / "certificate.png"
        target.write_bytes(shared.read_bytes())
        markdown = tmp_path / f"{document_id}_parsed.md"
        markdown.write_text(
            f"![证书]({image_dir.name}/certificate.png)", encoding="utf-8"
        )
        return build_document_artifacts(
            document_id=document_id,
            document_role=f"duplicate_{document_id}",
            original_filename=source.name,
            source_path=source,
            markdown_path=markdown,
            images_dir=image_dir,
            parsed_data={"text": markdown.read_text(encoding="utf-8"), "images": [{}]},
        )

    left = artifact("left")
    right = artifact("right")
    service = DuplicateCandidateService(
        DocumentDescriptor(
            id="left",
            filename="A.docx",
            path=str(tmp_path / "left_parsed.md"),
            evidence_blocks_path=left["evidence_blocks_path"],
        ),
        DocumentDescriptor(
            id="right",
            filename="B.docx",
            path=str(tmp_path / "right_parsed.md"),
            evidence_blocks_path=right["evidence_blocks_path"],
        ),
    )
    candidates = await service.build()
    image_candidate = next(candidate for candidate in candidates if candidate.image_score)
    assert image_candidate.image_score == pytest.approx(1.0)
    assert image_candidate.to_agent_dict()["image_comparison"]["left_image_sha256"]


def test_failed_scan_ocr_forces_partial_coverage(tmp_path: Path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"source")
    markdown = tmp_path / "scan_parsed.md"
    markdown.write_text("扫描页", encoding="utf-8")
    result = build_document_artifacts(
        document_id="scan",
        document_role="duplicate_left",
        original_filename=source.name,
        source_path=source,
        markdown_path=markdown,
        images_dir=None,
        parsed_data={
            "text": "扫描页",
            "page_count": 1,
            "parsed_page_count": 0,
            "scanned_page_count": 1,
            "ocr_page_count": 0,
            "failed_ocr_page_count": 1,
            "warnings": ["scan_page_ocr_unresolved"],
        },
    )
    assert result["coverage"].status == "partial"
    assert result["coverage"].failed_ocr_page_count == 1
