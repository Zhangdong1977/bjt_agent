"""Unit tests for the fresh technical-bid duplicate-check design."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.agent.duplicate_check_agent import DuplicateCheckAgent
from backend.schemas.duplicate_check import DuplicateFindingPayload
from backend.services.duplicate_candidates import (
    DocumentDescriptor,
    DuplicateCandidateService,
    calculate_evidence_strength,
    normalize_text,
    parse_markdown_blocks,
)
from backend.services.duplicate_result_grouper import group_duplicate_findings


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "duplicate"
RULE_DIR = Path(__file__).parents[2] / "docs" / "rules-duplicate"


def descriptor(name: str, filename: str) -> DocumentDescriptor:
    return DocumentDescriptor(id=name, filename=filename, path=str(FIXTURE_DIR / filename))


def test_normalize_text_removes_layout_noise_but_keeps_content():
    assert normalize_text(" 余必亲临，昼夜督造！AB-123 ") == "余必亲临昼夜督造ab123"


def test_markdown_blocks_preserve_section_and_line_location():
    blocks = parse_markdown_blocks(descriptor("left-doc", "a_bid.md"), "left")
    assert blocks
    personnel = next(block for block in blocks if "128" in block.text)
    assert personnel.side == "left"
    assert personnel.document_id == "left-doc"
    assert "拟投入项目人员情况" in personnel.section
    assert personnel.start_line > 0
    assert "128" in personnel.numbers


def test_number_and_model_extraction_allows_chinese_boundaries(tmp_path):
    markdown = tmp_path / "numbers.md"
    markdown.write_text(
        "# 服务承诺\n\n质保3年，响应30分钟，型号AB-123，证书AB12B0345。\n",
        encoding="utf-8",
    )
    blocks = parse_markdown_blocks(
        DocumentDescriptor(id="doc", filename="numbers.md", path=str(markdown)),
        "left",
    )

    assert blocks
    assert {"3", "30", "AB-123", "AB12B0345"}.issubset(set(blocks[0].numbers))


def test_short_or_repeated_text_has_lower_evidence_strength():
    short = calculate_evidence_strength(
        normalized_length=14,
        left_occurrences=5,
        right_occurrences=4,
        similarity_score=1.0,
    )
    long_unique = calculate_evidence_strength(
        normalized_length=160,
        left_occurrences=1,
        right_occurrences=1,
        similarity_score=0.92,
    )

    assert short < long_unique


@pytest.mark.asyncio
async def test_candidate_budget_ranks_long_unique_exact_text_above_short_text(tmp_path):
    short = "我方严格执行项目质量管理要求"
    long_text = "本项目采用分区部署、双链路校验、逐项回归和问题闭环机制，" * 5
    markdown = f"# 实施方案\n\n{short}\n\n{long_text}\n"
    left_path = tmp_path / "left.md"
    right_path = tmp_path / "right.md"
    left_path.write_text(markdown, encoding="utf-8")
    right_path.write_text(markdown, encoding="utf-8")
    service = DuplicateCandidateService(
        DocumentDescriptor(id="left", filename="left.md", path=str(left_path)),
        DocumentDescriptor(id="right", filename="right.md", path=str(right_path)),
    )

    candidates = await service.build()
    short_candidate = next(
        item for item in candidates if item.match_type == "exact" and item.left.text == short
    )
    long_candidate = next(
        item for item in candidates if item.match_type == "exact" and item.left.text == long_text
    )

    assert long_candidate.evidence_strength > short_candidate.evidence_strength
    assert long_candidate.rank_score > short_candidate.rank_score


@pytest.mark.asyncio
async def test_candidate_service_finds_exact_and_near_exact_pairs(tmp_path):
    service = DuplicateCandidateService(
        descriptor("left-doc", "a_bid.md"),
        descriptor("right-doc", "b_bid.md"),
    )
    candidates = await service.build()
    assert candidates
    assert any("余必亲临" in item.left.text and item.similarity_score == 1 for item in candidates)
    assert any("128" in item.left.text and item.match_type in {"near_exact", "structural"} for item in candidates)
    assert all(0 <= item.similarity_score <= 1 for item in candidates)

    matches = service.search("项目人员 主导项目 证书", limit=10)
    assert matches
    cache = tmp_path / "candidate-cache.json"
    service.save_cache(cache)
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["left_document_id"] == "left-doc"
    assert payload["candidates"]

    context = service.get_context(candidates[0].id)
    assert context
    assert context["left_context"]["current"]["id"] == candidates[0].left.id
    assert context["right_context"]["current"]["location"]["section"]


def test_duplicate_finding_schema_rejects_fabricated_score_and_verdict():
    valid = {
        "check_item_name": "人员承诺",
        "verdict": "suspicious",
        "similarity_score": 0.93,
        "match_type": "near_exact",
        "left_excerpt": "A 方证据",
        "right_excerpt": "B 方证据",
        "explanation": "非标准措辞一致",
    }
    assert DuplicateFindingPayload(**valid).verdict == "suspicious"
    with pytest.raises(ValidationError):
        DuplicateFindingPayload(**{**valid, "similarity_score": 1.1})
    with pytest.raises(ValidationError):
        DuplicateFindingPayload(**{**valid, "verdict": "illegal"})


def test_agent_only_accepts_structured_verdicts():
    parsed = DuplicateCheckAgent._parse_response(
        '```json\n[{"candidate_id":"abc","verdict":"reasonable"}]\n```'
    )
    assert parsed[0]["candidate_id"] == "abc"
    assert DuplicateCheckAgent._parse_response(
        '[{"candidate_id":"abc","verdict":"unknown"}]'
    )[0]["verdict"] == "unknown"
    with pytest.raises(ValueError):
        DuplicateCheckAgent._parse_response(
            '[{"candidate_id":"abc","verdict":"确定串标"}]'
        )


def test_agent_cannot_override_candidate_score_or_evidence():
    candidates = [
        {
            "candidate_id": "candidate-1",
            "similarity_score": 0.8765,
            "lexical_score": 0.8,
            "structure_score": 0.5,
            "match_type": "near_exact",
            "left_excerpt": "可信 A 方原文",
            "left_location": {"section": "A 章节", "start_line": 1},
            "right_excerpt": "可信 B 方原文",
            "right_location": {"section": "B 章节", "start_line": 2},
        }
    ]
    model_output = [
        {
            "candidate_id": "candidate-1",
            "verdict": "suspicious",
            "check_item_name": "测试项",
            "similarity_score": 1.0,
            "left_excerpt": "模型伪造 A",
            "right_excerpt": "模型伪造 B",
            "explanation": "需要复核",
        }
    ]

    finding = DuplicateCheckAgent._materialize_findings(
        model_output, candidates
    )[0]

    assert finding.similarity_score == pytest.approx(0.8765)
    assert finding.left_excerpt == "可信 A 方原文"
    assert finding.right_excerpt == "可信 B 方原文"


def test_agent_downgrades_unproven_tender_or_public_claims_to_unknown():
    candidates = [
        {
            "candidate_id": "candidate-1",
            "similarity_score": 0.95,
            "lexical_score": 0.9,
            "structure_score": 0.2,
            "evidence_strength": 0.8,
            "match_type": "near_exact",
            "source_basis": "bidder_authored",
            "left_excerpt": "双方自拟承诺内容",
            "left_location": {"section": "承诺", "start_line": 1},
            "right_excerpt": "双方自拟承诺内容",
            "right_location": {"section": "承诺", "start_line": 2},
        }
    ]
    model_output = [
        {
            "candidate_id": "candidate-1",
            "verdict": "suspicious",
            "source_basis": "tender",
            "explanation": "该内容属于非招标强制内容。",
        }
    ]

    finding = DuplicateCheckAgent._materialize_findings(model_output, candidates)[0]

    assert finding.verdict == "unknown"
    assert finding.source_basis == "unknown"


def _finding(
    left_excerpt: str,
    right_excerpt: str,
    *,
    left_line: int,
    right_line: int,
) -> DuplicateFindingPayload:
    return DuplicateFindingPayload(
        check_item_name="连续内容",
        verdict="suspicious",
        source_basis="bidder_authored",
        similarity_score=0.96,
        match_type="near_exact",
        left_excerpt=left_excerpt,
        left_location={
            "section": "实施方案",
            "start_line": left_line,
            "end_line": left_line,
        },
        right_excerpt=right_excerpt,
        right_location={
            "section": "实施方案",
            "start_line": right_line,
            "end_line": right_line,
        },
        explanation="双方片段高度相似",
        evidence={"candidate_id": f"{left_line}-{right_line}", "evidence_strength": 0.8},
    )


def test_mirrored_findings_are_collapsed_with_occurrences():
    grouped = group_duplicate_findings(
        [
            _finding("甲方片段", "乙方片段", left_line=10, right_line=20),
            _finding("乙方片段", "甲方片段", left_line=30, right_line=40),
        ]
    )

    assert len(grouped) == 1
    assert grouped[0].evidence["collapsed_count"] == 2
    assert len(grouped[0].evidence["occurrences"]) == 2


def test_contiguous_findings_are_aggregated_into_one_chapter_finding():
    grouped = group_duplicate_findings(
        [
            _finding("第一段独特实施内容", "第一段独特实施内容", left_line=10, right_line=20),
            _finding("第二段独特实施内容", "第二段独特实施内容", left_line=12, right_line=22),
        ]
    )

    assert len(grouped) == 1
    assert grouped[0].left_location["start_line"] == 10
    assert grouped[0].left_location["end_line"] == 12
    assert grouped[0].evidence["aggregated_count"] == 2


def test_first_rule_set_contains_confirmed_personnel_examples():
    rules = sorted(RULE_DIR.glob("*.md"))
    assert len(rules) >= 4
    personnel = (RULE_DIR / "D001 拟投入项目人员情况.md").read_text(encoding="utf-8")
    assert "主导项目经验" in personnel
    assert "社保缴纳单位" in personnel
    assert "余必亲临，昼夜督造" in personnel
    assert "学历、学位" in personnel
