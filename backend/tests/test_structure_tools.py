"""Unit tests for structure tools (SectionContentTool, ImageOcrTool, _create_shared_loaders).

Bug coverage:
- SectionContentTool must merge results across multiple docs (not short-circuit on first match)
- ImageOcrTool must require doc_name when multiple docs of the same type exist
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.agent.tools.structure_tools import (
    ImageOcrTool,
    SectionContentTool,
    _create_shared_loaders,
)


def _write_doc(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# SectionContentTool — multi-doc merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_section_content_tool_merges_multi_doc_results(tmp_path):
    """Two tender docs both contain an s1 section — both must be returned, not just the first."""
    doc_a = tmp_path / "tender_a.md"
    doc_b = tmp_path / "tender_b.md"
    _write_doc(
        doc_a,
        "# 第1章 概述\n\nA 文档的内容。\n",
    )
    _write_doc(
        doc_b,
        "# 第1章 概述\n\nB 文档的内容。\n",
    )
    loaders = _create_shared_loaders(
        tender_docs=[("a.pdf", str(doc_a)), ("b.pdf", str(doc_b))],
        bid_docs=[],
    )
    tool = SectionContentTool(loaders=loaders)

    result = await tool.execute(doc_type="tender", section_id="s1")

    assert result.success is True, f"expected success, got error={result.error!r}"
    # Critical assertion: both docs' content must appear
    assert "A 文档的内容" in result.content, (
        f"missing doc A content; got: {result.content[:200]!r}"
    )
    assert "B 文档的内容" in result.content, (
        f"missing doc B content; got: {result.content[:200]!r}"
    )
    # sources list must include both doc names
    sources = result.data["sources"] if isinstance(result.data, dict) else None
    assert sources is not None, f"expected data.sources list, got: {result.data!r}"
    assert "a.pdf" in sources
    assert "b.pdf" in sources


@pytest.mark.asyncio
async def test_section_content_tool_single_doc_keeps_legacy_shape(tmp_path):
    """Single doc should keep source_doc (singular) for backward compat."""
    doc = tmp_path / "solo.md"
    _write_doc(doc, "# 章节\n\n唯一的内容。\n")
    loaders = _create_shared_loaders(
        tender_docs=[("solo.pdf", str(doc))],
        bid_docs=[],
    )
    tool = SectionContentTool(loaders=loaders)

    result = await tool.execute(doc_type="tender", section_id="s1")

    assert result.success is True
    assert "唯一的内容" in result.content
    # Single-doc: source_doc (singular) present
    assert result.data.get("source_doc") == "solo.pdf"
    # sources list NOT present in single-doc mode
    assert "sources" not in result.data


# ---------------------------------------------------------------------------
# ImageOcrTool — multi-doc disambiguation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_ocr_tool_multi_doc_requires_doc_name(tmp_path):
    """When multiple tender docs exist, OCR must require doc_name to disambiguate."""
    # Create two docs (the parsed-md files don't need to exist for the validation
    # check, but doc loaders lazy-load content so they just need to exist).
    doc_a = tmp_path / "a.md"
    doc_b = tmp_path / "b.md"
    _write_doc(doc_a, "# a\n")
    _write_doc(doc_b, "# b\n")

    loaders = _create_shared_loaders(
        tender_docs=[("a.pdf", str(doc_a)), ("b.pdf", str(doc_b))],
        bid_docs=[],
    )
    tool = ImageOcrTool(loaders=loaders, ocr_service_url="http://stub")

    # No doc_name provided — must fail with informative error
    result = await tool.execute(doc_type="tender", image_path="images/foo.png")

    assert result.success is False
    err = result.error.lower()
    assert "doc_name" in err or "multiple" in err, f"error missing hint: {result.error!r}"
    assert "a.pdf" in result.error
    assert "b.pdf" in result.error


@pytest.mark.asyncio
async def test_image_ocr_tool_single_doc_works_without_doc_name(tmp_path):
    """Single doc: passing only image_path should work and attribute source_doc."""
    img_rel = "images/solo.png"
    img_path = tmp_path / img_rel
    img_path.parent.mkdir(parents=True, exist_ok=True)
    img_path.write_bytes(b"fake-png")
    doc = tmp_path / "solo.md"
    doc.write_text("# doc\n", encoding="utf-8")

    loaders = _create_shared_loaders(
        tender_docs=[("solo.pdf", str(doc))],
        bid_docs=[],
    )
    tool = ImageOcrTool(loaders=loaders, ocr_service_url="http://stub")

    with patch.object(tool, "_remote_ocr", new=AsyncMock(return_value="识别到的文字")):
        result = await tool.execute(doc_type="tender", image_path=img_rel)

    assert result.success is True
    assert "识别到的文字" in result.content
    assert result.data["source_doc"] == "solo.pdf"


@pytest.mark.asyncio
async def test_remote_ocr_retries_service_failure_three_times(monkeypatch, tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"fake-png")
    attempts = 0

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts <= 3:
                return _Response({"success": False, "error": "temporary failure"})
            return _Response({"success": True, "ocr_text": "识别成功"})

    sleep = AsyncMock()
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr("backend.agent.tools.structure_tools.asyncio.sleep", sleep)
    tool = ImageOcrTool(loaders={}, ocr_service_url="http://ocr-service")

    result = await tool._remote_ocr(image)

    assert result == "识别成功"
    assert attempts == 4
    assert sleep.await_count == 3
