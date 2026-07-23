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
    normalize_text,
    parse_markdown_blocks,
)


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


def test_first_rule_set_contains_confirmed_personnel_examples():
    rules = sorted(RULE_DIR.glob("*.md"))
    assert len(rules) >= 4
    personnel = (RULE_DIR / "D001 拟投入项目人员情况.md").read_text(encoding="utf-8")
    assert "主导项目经验" in personnel
    assert "社保缴纳单位" in personnel
    assert "余必亲临，昼夜督造" in personnel
    assert "学历、学位" in personnel
