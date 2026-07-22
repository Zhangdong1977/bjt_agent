"""Pure unit tests for duplicate-check planning and analysis helpers."""

from pathlib import Path

import pytest

from backend.schemas.duplicate_check import DuplicateAgentResult
from backend.services.duplicate_retrieval import recall_candidates
from backend.services.duplicate_rule_loader import load_duplicate_rule
from backend.services.duplicate_structure import build_structure_index, read_section
from backend.tasks.duplicate_tasks import build_document_pairs
from backend.main import app


def test_document_pair_counts_are_unique_and_stable():
    assert len(build_document_pairs(["b", "a"])) == 1
    assert len(build_document_pairs(["a", "b", "c"])) == 3
    assert len(build_document_pairs(["a", "b", "c", "d"])) == 6
    pairs = build_document_pairs(["e", "d", "c", "b", "a", "a"])
    assert len(pairs) == 10
    assert len(set(pairs)) == len(pairs)
    assert all(a < b for a, b in pairs)


def test_structure_index_distinguishes_real_headings(tmp_path: Path):
    structured = tmp_path / "structured.md"
    structured.write_text("# 一\n内容\n## 二\n子内容\n## 三\n子内容\n# 四\n尾部", encoding="utf-8")
    _, data = build_structure_index(structured)
    assert data["quality"] == "reliable"
    assert len(data["sections"]) == 4
    assert read_section(structured, data["sections"][0]) == "内容\n## 二\n子内容\n## 三\n子内容"
    assert read_section(structured, data["sections"][1]) == "子内容"

    flat = tmp_path / "flat.md"
    flat.write_text("第一段\n\n第二段\n\n第三段", encoding="utf-8")
    _, flat_data = build_structure_index(flat)
    assert flat_data["quality"] == "none"
    assert flat_data["sections"] == []


def test_rule_template_loads():
    path = Path(__file__).resolve().parents[2] / "docs" / "rules-duplicate" / "duplicate-check.md"
    rule = load_duplicate_rule(path)
    assert rule.config.chunk_size_chars == 1200
    assert len(rule.sha256) == 64
    assert "合理重复" in rule.instructions


@pytest.mark.asyncio
async def test_lexical_recall_and_exclusion(tmp_path: Path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    shared = "本项目采用独有的三级巡检流程，先校验蓝色控制阀，再记录设备序列号码。" * 5
    a.write_text(shared + "\n\nA 的其它内容" * 30, encoding="utf-8")
    b.write_text(shared + "\n\nB 的其它内容" * 30, encoding="utf-8")
    rule_path = Path(__file__).resolve().parents[2] / "docs" / "rules-duplicate" / "duplicate-check.md"
    config = load_duplicate_rule(rule_path).config.model_copy(update={"lexical_threshold": 0.6})
    candidates, diagnostics = await recall_candidates(str(a), str(b), config, use_embeddings=False)
    assert diagnostics["candidate_count"] >= 1
    assert candidates[0]["document_a"]["excerpt"]
    assert "internal_score" in candidates[0]


def test_result_requires_two_sided_evidence():
    with pytest.raises(ValueError):
        DuplicateAgentResult.model_validate({
            "conclusion": "suspicious_duplicate",
            "summary": "发现问题",
            "matches": [],
        })


def test_duplicate_api_is_registered():
    paths = app.openapi()["paths"]
    assert "/api/projects/{project_id}/duplicate-check/tasks" in paths
    assert "/api/projects/{project_id}/duplicate-check/tasks/{task_id}/results" in paths
    assert "/api/projects/{project_id}/duplicate-check/tasks/{task_id}/retry-failed" in paths
