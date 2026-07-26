from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.evaluation.duplicate_batch_stress import run_batch_stress
from backend.evaluation.duplicate_release_gate import evaluate_release_gate
from backend.schemas.duplicate_check import DuplicateFindingPayload
from backend.services.duplicate_batch import MultiDocumentCandidateService
from backend.services.duplicate_candidates import DocumentDescriptor
from backend.services.duplicate_hash import find_identical_content_groups
from backend.services.duplicate_result_grouper import group_duplicate_findings
from backend.services.duplicate_runtime import snapshot_for_task


@pytest.mark.asyncio
async def test_batch_index_forms_one_exact_cluster_and_keeps_all_occurrences(tmp_path: Path):
    descriptors = []
    shared = "这是一个跨文档重复的长段落，包含设备型号 ZX-9000 和响应时间 30 分钟。"
    for index in range(4):
        path = tmp_path / f"bid-{index}.md"
        path.write_text(f"# 方案\n\n{shared}\n\n文档专属参数 {index + 1}。", encoding="utf-8")
        descriptors.append(
            DocumentDescriptor(
                id=f"doc-{index}",
                filename=path.name,
                path=str(path),
                role="duplicate_bid",
            )
        )
    service = MultiDocumentCandidateService(descriptors, semantic_enabled=False)
    candidates = await service.build()
    cluster = next(item for item in service.exact_clusters if len(item["document_ids"]) == 4)
    assert len(cluster["occurrences"]) == 4
    # The exact cluster contributes one representative candidate, not six
    # independent LLM candidates.
    assert sum(candidate.match_type == "exact" for candidate in candidates) == 1
    assert service.pair_statistics()[("doc-0", "doc-3")]["candidate_count"] >= 1


def test_batch_hash_guard_hashes_groups_without_pairwise_api(tmp_path: Path):
    same_a = tmp_path / "a.bin"
    same_b = tmp_path / "b.bin"
    other = tmp_path / "c.bin"
    same_a.write_bytes(b"same")
    same_b.write_bytes(b"same")
    other.write_bytes(b"other")
    groups = find_identical_content_groups(
        [("a", same_a, None), ("b", same_b, None), ("c", other, None)]
    )
    assert groups and groups[0][0] == "original"
    assert groups[0][1] == ["a", "b"]


def test_runtime_snapshot_is_immutable_after_task_creation():
    settings = SimpleNamespace(
        duplicate_algorithm_version="test-v1",
        duplicate_batch_enabled=True,
        duplicate_semantic_enabled=False,
        duplicate_ocr_enabled=True,
        duplicate_remote_ocr_enabled=False,
        duplicate_vision_enabled=False,
        duplicate_semantic_min_score=0.72,
        duplicate_candidate_min_score=0.45,
        duplicate_lexical_min_score=0.16,
        duplicate_structure_min_score=0.5,
        duplicate_near_exact_min_score=0.72,
        duplicate_image_min_score=0.78,
        duplicate_embedding_batch_size=32,
        duplicate_embedding_max_blocks=400,
        duplicate_embedding_max_input_chars=1000,
        duplicate_embedding_min_chars=24,
        duplicate_ocr_max_images=24,
        duplicate_remote_ocr_max_calls=4,
        duplicate_vision_max_calls=2,
        duplicate_pair_max_candidates=400,
        duplicate_batch_max_candidates=1200,
        duplicate_embedding_timeout_seconds=45,
        agent_total_timeout=5400,
    )
    task = SimpleNamespace()
    snapshot = snapshot_for_task(task, settings)
    settings.duplicate_semantic_enabled = True
    assert task.duplicate_feature_snapshot == snapshot
    assert snapshot["features"]["semantic"] is False


@pytest.mark.asyncio
async def test_ten_document_stress_has_single_global_flow():
    report = await run_batch_stress(document_count=10, rule_flow_count=5, repeated_paragraphs=4)
    assert report["document_pair_count"] == 45
    assert report["shared_cluster_occurrence_count"] == 10
    assert report["independent_llm_flows"] == 5
    assert report["pairwise_llm_flow_baseline"] == 225
    assert report["n_squared_llm_flows"] is False


def test_release_gate_reports_missing_category_as_failure():
    report = {
        "algorithm_version": "test-v1",
        "metrics": {"precision_at_k": 1.0, "peak_memory_mb": 1, "llm_calls": 0},
        "category_metrics": {"text": {"recall_at_k": 1.0}},
    }
    result = evaluate_release_gate(report)
    assert result["passed"] is False
    assert any("category:table" == item["name"] for item in result["checks"])


def test_grouped_occurrences_keep_document_identity():
    def finding(left_doc: str, right_doc: str, left_line: int) -> DuplicateFindingPayload:
        return DuplicateFindingPayload(
            check_item_name="重复段落",
            verdict="suspicious",
            source_basis="bidder_authored",
            similarity_score=0.9,
            match_type="near_exact",
            left_excerpt="同一段落内容",
            left_location={"section": "方案", "start_line": left_line, "end_line": left_line},
            right_excerpt="同一段落内容",
            right_location={"section": "方案", "start_line": left_line + 1, "end_line": left_line + 1},
            explanation="重复",
            evidence={
                "candidate_id": f"{left_doc}-{right_doc}",
                "left_document_id": left_doc,
                "right_document_id": right_doc,
                "occurrences": [
                    {
                        "left_document_id": left_doc,
                        "right_document_id": right_doc,
                        "left_block_id": f"{left_doc}:b",
                        "right_block_id": f"{right_doc}:b",
                        "left_excerpt": "同一段落内容",
                        "right_excerpt": "同一段落内容",
                        "left_location": {"section": "方案", "start_line": left_line},
                        "right_location": {"section": "方案", "start_line": left_line + 1},
                    }
                ],
            },
        )

    grouped = group_duplicate_findings(
        [finding("a", "b", 10), finding("c", "d", 10)]
    )
    assert len(grouped) == 1
    assert {
        (item["left_document_id"], item["right_document_id"])
        for item in grouped[0].evidence["occurrences"]
    } == {("a", "b"), ("c", "d")}
