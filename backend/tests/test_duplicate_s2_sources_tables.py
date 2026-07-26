"""S2-1 source provenance and structured-table channel tests."""

from pathlib import Path

import pytest

from backend.agent.duplicate_check_agent import DuplicateCheckAgent
from backend.services.document_artifacts import build_document_artifacts, load_evidence_blocks
from backend.services.duplicate_candidates import DocumentDescriptor, DuplicateCandidateService
from backend.services.duplicate_sources import DuplicateSourceIndex, SourceDocumentDescriptor
from backend.services.duplicate_tables import compare_table_blocks


def _artifact(tmp_path: Path, document_id: str, role: str, markdown: str) -> dict:
    source = tmp_path / f"{document_id}.docx"
    source.write_bytes(f"source-{document_id}".encode())
    parsed = tmp_path / f"{document_id}_parsed.md"
    parsed.write_text(markdown, encoding="utf-8")
    return build_document_artifacts(
        document_id=document_id,
        document_role=role,
        original_filename=source.name,
        source_path=source,
        markdown_path=parsed,
        images_dir=None,
        parsed_data={
            "text": markdown,
            "source_basis": (
                "tender" if role == "duplicate_tender" else "bidder_authored"
            ),
        },
    )


@pytest.mark.asyncio
async def test_source_index_only_returns_traceable_snapshot_blocks(tmp_path: Path):
    artifact = _artifact(
        tmp_path,
        "tender-1",
        "duplicate_tender",
        "# 设备要求\n\n核心交换机端口数量不得少于 48 个。",
    )
    index = DuplicateSourceIndex(
        [
            SourceDocumentDescriptor(
                id="tender-1",
                filename="招标文件.docx",
                evidence_blocks_path=artifact["evidence_blocks_path"],
                source_basis="tender",
                snapshot_hash="a" * 64,
                version="upload-aaaaaaaaaaaa",
            )
        ]
    )

    assert await index.build() == 2
    matches = index.search("交换机 48 个端口", source_basis="tender")
    assert matches
    payload = matches[0].to_agent_dict()
    assert payload["source_document_id"] == "tender-1"
    assert payload["source_snapshot_hash"] == "a" * 64
    assert payload["source_version"] == "upload-aaaaaaaaaaaa"
    assert index.get_context(payload["source_reference_id"])["context"]["current"]


@pytest.mark.asyncio
async def test_source_index_rejects_unversioned_source(tmp_path: Path):
    artifact = _artifact(tmp_path, "public-1", "duplicate_tender", "公共模板内容")
    index = DuplicateSourceIndex(
        [
            SourceDocumentDescriptor(
                id="public-1",
                filename="模板.docx",
                evidence_blocks_path=artifact["evidence_blocks_path"],
                source_basis="public",
                snapshot_hash=None,
                version=None,
            )
        ]
    )
    assert await index.build() == 0
    assert index.warnings == ["source_snapshot_incomplete:public-1"]


def test_table_channel_locates_rare_cells_and_numeric_signature(tmp_path: Path):
    left = _artifact(
        tmp_path,
        "left",
        "duplicate_left",
        "| 型号 | 数量 | 单位 | 备注 |\n|---|---:|---|---|\n| ZX-9X | 12 | 桶 | 内部代号MARS-77 |",
    )
    right = _artifact(
        tmp_path,
        "right",
        "duplicate_right",
        "| 型号 | 数量 | 单位 | 备注 |\n|---|---:|---|---|\n| ZX-9X | 12 | 桶 | 内部代号MARS-77 |",
    )
    comparisons = compare_table_blocks(
        load_evidence_blocks(left["evidence_blocks_path"]),
        load_evidence_blocks(right["evidence_blocks_path"]),
    )
    assert comparisons
    comparison = comparisons[0]
    assert comparison.header_similarity == pytest.approx(1.0)
    assert comparison.numeric_signature_score == pytest.approx(1.0)
    assert "内部代号mars-77" in comparison.shared_rare_cells
    assert comparison.to_dict()["left"]["cells"][2] == "桶"


@pytest.mark.asyncio
async def test_candidate_service_prefers_persisted_ir_over_markdown_fallback(tmp_path: Path):
    left = _artifact(tmp_path, "left-ir", "duplicate_left", "IR 中的异常型号 ZX-001")
    right = _artifact(tmp_path, "right-ir", "duplicate_right", "IR 中的异常型号 ZX-001")
    legacy_left = tmp_path / "left.md"
    legacy_right = tmp_path / "right.md"
    legacy_left.write_text("完全无关的旧 Markdown A", encoding="utf-8")
    legacy_right.write_text("完全无关的旧 Markdown B", encoding="utf-8")
    service = DuplicateCandidateService(
        DocumentDescriptor(
            id="left-ir",
            filename="A.docx",
            path=str(legacy_left),
            evidence_blocks_path=left["evidence_blocks_path"],
        ),
        DocumentDescriptor(
            id="right-ir",
            filename="B.docx",
            path=str(legacy_right),
            evidence_blocks_path=right["evidence_blocks_path"],
        ),
    )
    candidates = await service.build()
    assert any("ZX-001" in candidate.left.text for candidate in candidates)


def test_agent_accepts_tender_basis_only_with_matching_snapshot_reference():
    candidate = {
        "candidate_id": "candidate-1",
        "similarity_score": 0.91,
        "lexical_score": 0.9,
        "structure_score": 0.1,
        "match_type": "near_exact",
        "source_basis": "bidder_authored",
        "left_excerpt": "交换机端口不少于48个",
        "left_location": {"start_line": 1},
        "right_excerpt": "交换机端口不少于48个",
        "right_location": {"start_line": 2},
    }
    source = {
        "source_reference_id": "source-1",
        "source_basis": "tender",
        "source_document_id": "tender-1",
        "source_block_id": "tender-1:b:000001",
        "source_snapshot_hash": "a" * 64,
        "source_version": "upload-aaaaaaaaaaaa",
        "source_excerpt": "端口不得少于48个",
    }
    finding = DuplicateCheckAgent._materialize_findings(
        [
            {
                "candidate_id": "candidate-1",
                "verdict": "reasonable",
                "source_basis": "tender",
                "source_reference_id": "source-1",
                "explanation": "该重复来自招标文件明确要求。",
            }
        ],
        [candidate],
        [source],
    )[0]
    assert finding.verdict == "reasonable"
    assert finding.source_basis == "tender"
    assert finding.evidence["source_reference"]["source_document_id"] == "tender-1"


def test_s2_1_migration_contains_source_snapshot_columns():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "027_add_duplicate_sources.sql"
    ).read_text(encoding="utf-8")
    assert "source_snapshot_hash" in migration
    assert "source_version" in migration
    assert "duplicate_public_reference" in migration
