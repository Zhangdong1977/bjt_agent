"""S2-0 deterministic evidence and parser artifact tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.models import Document
from backend.services.document_artifacts import (
    build_document_artifacts,
    build_evidence_blocks,
    combine_coverage_summaries,
    load_artifact_manifest,
    load_evidence_blocks,
    normalize_text,
)


def test_normalize_text_is_stable_for_whitespace_and_width():
    assert normalize_text("  ＡＢＣ\n  １２３  ") == "abc 123"


def test_build_evidence_blocks_keeps_locations_and_table_structure(tmp_path: Path):
    image = tmp_path / "figure.png"
    image.write_bytes(b"fake-image")
    markdown = (
        "# 技术方案\n\n"
        "型号 ABC-123，数量 10 台。\n\n"
        "| 型号 | 数量 |\n"
        "|---|---:|\n"
        "| X-1 | 2 |\n\n"
        "![现场图](figure.png)\n"
    )

    blocks = build_evidence_blocks(
        markdown,
        document_id="doc-1",
        document_role="duplicate_left",
        images_dir=tmp_path,
        source_path=tmp_path / "source.docx",
    )

    assert [block.content_type for block in blocks] == [
        "heading",
        "paragraph",
        "table",
        "table_row",
        "image",
    ]
    assert blocks[2].table_id == "table-0001"
    assert blocks[3].row_index == 0
    assert blocks[3].header_map == {"0": "型号", "1": "数量"}
    assert blocks[4].image_sha256
    assert blocks[1].numbers == ["123", "10"]
    assert blocks[0].section_path == ["技术方案"]


def test_metadata_keeps_case_insensitive_units():
    blocks = build_evidence_blocks(
        "设备容量 2 TB，长度 3 m。",
        document_id="doc-units",
    )

    assert blocks[0].units == ["tb", "m"]


def test_build_and_reload_manifest(tmp_path: Path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    markdown = tmp_path / "source_parsed.md"
    markdown.write_text("# 标题\n\n正文 20 kg", encoding="utf-8")

    result = build_document_artifacts(
        document_id="doc-2",
        document_role="duplicate_right",
        original_filename="source.pdf",
        source_path=source,
        markdown_path=markdown,
        images_dir=None,
        parsed_data={
            "text": markdown.read_text(encoding="utf-8"),
            "page_count": 2,
            "parsed_page_count": 2,
            "parser_name": "markitdown",
            "parser_version": "test",
        },
    )

    manifest = load_artifact_manifest(result["manifest_path"])
    blocks = load_evidence_blocks(result["evidence_blocks_path"])
    assert manifest is not None
    assert manifest.schema_version.endswith("/v1")
    assert manifest.coverage.status == "complete"
    assert manifest.source.sha256
    assert manifest.evidence_block_count == len(blocks) == 2
    assert all(Path(path).exists() for path in (result["manifest_path"], result["evidence_blocks_path"]))


def test_artifact_api_shape_does_not_return_workspace_paths(tmp_path: Path):
    from backend.api.documents import _document_artifacts_response

    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    markdown = tmp_path / "source_parsed.md"
    markdown.write_text("正文 20 kg", encoding="utf-8")
    result = build_document_artifacts(
        document_id="doc-api",
        document_role="duplicate_left",
        original_filename="source.pdf",
        source_path=source,
        markdown_path=markdown,
        images_dir=None,
        parsed_data={"text": markdown.read_text(encoding="utf-8")},
    )
    document = SimpleNamespace(
        id="doc-api",
        artifact_manifest_path=result["manifest_path"],
        evidence_blocks_path=result["evidence_blocks_path"],
        coverage_summary=result["coverage"].model_dump(mode="json"),
    )

    response = _document_artifacts_response(document, include_blocks=True, limit=10)
    payload = response.model_dump(mode="json")

    assert response.block_count == 1
    assert str(tmp_path) not in str(payload)
    assert "manifest_path" not in payload
    assert "evidence_blocks_path" not in payload


def test_combine_coverage_never_treats_missing_as_complete():
    status, warnings = combine_coverage_summaries(
        [{"status": "complete"}, None, {"status": "partial", "warnings": ["page_missing"]}]
    )
    assert status == "insufficient"
    assert warnings == ["page_missing"]


def test_combine_coverage_rejects_malformed_summary():
    status, warnings = combine_coverage_summaries(["not-a-summary"])

    assert status == "insufficient"
    assert warnings == ["coverage_summary_invalid"]


@pytest.mark.asyncio
async def test_save_parsed_content_persists_docling_and_artifact_paths(tmp_path: Path, monkeypatch):
    from backend.tasks import document_parser

    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    docling = tmp_path / "source_docling.json"
    docling.write_text("{}", encoding="utf-8")
    document = Document(
        id="doc-3",
        doc_type="duplicate_left",
        original_filename="source.pdf",
        file_path=str(source),
        status="pending",
    )
    monkeypatch.setattr(document_parser, "_publish_parse_progress", lambda *args, **kwargs: None)

    result = await document_parser._save_parsed_content(
        source,
        {
            "text": "# 标题\n\n内容 3 台",
            "images": [],
            "page_count": 1,
            "docling_json_path": str(docling),
            "parser_name": "docling",
            "parser_version": "test",
        },
        document,
        settings=None,
        document_id=document.id,
    )

    assert document.docling_json_path == str(docling)
    assert document.artifact_manifest_path
    assert document.evidence_blocks_path
    assert document.coverage_summary["status"] == "complete"
    assert result["coverage_status"] == "complete"
    assert Path(document.artifact_manifest_path).exists()
    assert Path(document.evidence_blocks_path).exists()
