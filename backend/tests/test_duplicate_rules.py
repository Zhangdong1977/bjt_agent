"""Contract, routing and synthetic recall tests for the complete duplicate rule set."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.agent.duplicate_check_agent as duplicate_check_module
from backend.agent.duplicate_check_agent import DuplicateCheckAgent
from backend.agent.duplicate_master_agent import DuplicateMasterAgent
from backend.services.duplicate_candidates import DocumentDescriptor, DuplicateCandidateService
from backend.services.duplicate_rules import (
    RuleValidationError,
    build_rule_query,
    extract_check_items,
    filter_candidate_payloads,
    load_duplicate_rule,
    load_duplicate_rules,
    parse_duplicate_rule,
)


RULE_DIR = Path(__file__).parents[1].parent / "docs" / "rules-duplicate"
CASE_FILE = Path(__file__).parent / "fixtures" / "duplicate" / "rule_cases.json"
EXPECTED_IDS = [f"D{index:03d}" for index in range(1, 11)]
CASES = {item["rule_id"]: item for item in json.loads(CASE_FILE.read_text(encoding="utf-8"))}


def test_rule_library_has_contiguous_unique_ids_and_one_file_per_rule():
    rules = load_duplicate_rules(RULE_DIR)
    assert [rule.rule_id for rule in rules] == EXPECTED_IDS
    assert len({rule.rule_id for rule in rules}) == len(rules)
    assert len(rules) == len(list(RULE_DIR.glob("*.md")))


@pytest.mark.parametrize("rule_id", EXPECTED_IDS)
def test_each_rule_has_machine_readable_contract_and_complete_sections(rule_id: str):
    rule = load_duplicate_rule(next(RULE_DIR.glob(f"{rule_id} *.md")))
    assert rule.version
    assert rule.title
    assert rule.candidate_types
    assert rule.channels
    assert 8 <= rule.max_candidates <= 50
    assert 0 <= rule.context_candidates <= rule.max_candidates
    assert 0.0 <= rule.min_evidence_strength <= 1.0
    assert len(rule.search_terms) >= 4

    required_sections = (
        "检查目标",
        "适用证据与检索通道",
        "合理重复",
        "疑似不合理重复",
        "排除与降级",
        "输出要求",
    )
    for section in required_sections:
        assert section in rule.body, f"{rule_id} 缺少 {section}"
    assert "证据不足" in rule.body
    assert "unknown" in rule.body
    assert "不得作出确定性的违法结论" in rule.body


@pytest.mark.parametrize("rule_id", EXPECTED_IDS)
def test_check_item_numbers_are_contiguous_and_sufficient(rule_id: str):
    rule = load_duplicate_rule(next(RULE_DIR.glob(f"{rule_id} *.md")))
    items = extract_check_items(rule.body)
    assert len(items) >= 5
    assert [item["number"] for item in items] == list(range(1, len(items) + 1))
    assert all(len(item["title"]) >= 2 for item in items)


def test_library_covers_business_domains_and_evidence_channels():
    text = "\n".join(path.read_text(encoding="utf-8") for path in RULE_DIR.glob("*.md"))
    for keyword in (
        "项目理解",
        "需求分析",
        "总体架构",
        "资质",
        "类似业绩",
        "知识产权",
        "进度",
        "安全",
        "环保",
        "应急",
        "测试",
        "调试",
        "验收",
        "交付",
        "培训",
        "迁移",
        "数据安全",
        "系统集成",
        "运维",
        "OCR",
        "复制残留",
        "跨文档",
    ):
        assert keyword in text, f"规则库缺少业务域：{keyword}"

    rules = load_duplicate_rules(RULE_DIR)
    all_types = {value for rule in rules for value in rule.candidate_types}
    all_channels = {value for rule in rules for value in rule.channels}
    assert {"paragraph", "table", "table_row", "image_ocr"} <= all_types
    assert {"lexical", "structure", "semantic", "image"} <= all_channels


def test_parser_rejects_missing_or_invalid_front_matter(tmp_path: Path):
    invalid = tmp_path / "bad.md"
    invalid.write_text("# bad\n\n### 检查项1：缺少元数据", encoding="utf-8")
    with pytest.raises(RuleValidationError):
        load_duplicate_rule(invalid)

    with pytest.raises(RuleValidationError):
        parse_duplicate_rule(
            "---\nrule_id: D999\nversion: 1\n---\n# D999\n\n### 检查项1：x"
        )


@pytest.mark.asyncio
async def test_master_fails_closed_before_creating_todos_for_invalid_library(tmp_path: Path):
    (tmp_path / "bad.md").write_text("# 没有规则元数据", encoding="utf-8")
    agent = DuplicateMasterAgent(
        project_id="project",
        task_id="task",
        user_id="user",
        rule_library_path=str(tmp_path),
        left_document_id="left",
        right_document_id="right",
        candidate_service=None,
        source_index=None,
        session_factory=None,
        max_concurrency=1,
    )

    result = await agent.run()

    assert result["success"] is False
    assert "规则库校验失败" in result["error"]


def test_filter_routes_content_type_channel_and_evidence_strength():
    rule = load_duplicate_rule(RULE_DIR / "D003 设备材料配置与技术参数.md")
    base = {
        "candidate_id": "target",
        "similarity_score": 0.96,
        "evidence_strength": 0.75,
        "lexical_score": 0.90,
        "structure_score": 0.88,
        "semantic_score": 0.0,
        "image_score": 0.0,
        "match_type": "structural",
        "left_excerpt": "ZX-9X 参数表",
        "right_excerpt": "ZX-9X 参数表",
        "left_location": {"content_type": "table_row"},
        "right_location": {"content_type": "table_row"},
    }
    rejected_type = {
        **base,
        "candidate_id": "heading",
        "left_location": {"content_type": "heading"},
        "right_location": {"content_type": "heading"},
    }
    rejected_score = {**base, "candidate_id": "weak", "evidence_strength": 0.01}
    rejected_channel = {
        **base,
        "candidate_id": "semantic",
        "structure_score": 0.0,
        "lexical_score": 0.0,
        "semantic_score": 0.95,
        "match_type": "semantic",
    }

    # Use a deliberately narrower routing contract to prove that a candidate
    # from an undeclared channel is excluded; the production D003 contract
    # also enables semantic product-description comparisons.
    selected = filter_candidate_payloads(
        replace(rule, channels=("lexical", "structure")),
        [rejected_type, rejected_score, rejected_channel, base],
    )
    assert [item["candidate_id"] for item in selected] == ["target"]


def test_image_and_semantic_rules_accept_their_declared_channels():
    image_rule = load_duplicate_rule(
        RULE_DIR / "D009 企业资质、类似业绩、证书与知识产权.md"
    )
    image_candidate = {
        "candidate_id": "image-target",
        "similarity_score": 1.0,
        # Image blocks may have no normalized text, so their generic textual
        # evidence strength can be low even when the SHA-256 is an exact match.
        "evidence_strength": 0.05,
        "lexical_score": 0.0,
        "structure_score": 0.0,
        "semantic_score": 0.0,
        "image_score": 1.0,
        "match_type": "exact",
        "left_excerpt": "",
        "right_excerpt": "",
        "left_location": {"content_type": "image"},
        "right_location": {"content_type": "image"},
        "image_comparison": {
            "image_score": 1.0,
            "left_image_sha256": "a" * 64,
            "right_image_sha256": "a" * 64,
        },
    }
    assert filter_candidate_payloads(image_rule, [image_candidate]) == [image_candidate]

    semantic_rule = load_duplicate_rule(
        RULE_DIR / "D005 项目理解、需求响应与总体架构.md"
    )
    semantic_candidate = {
        **image_candidate,
        "candidate_id": "semantic-target",
        "similarity_score": 0.86,
        "evidence_strength": 0.40,
        "semantic_score": 0.89,
        "image_score": 0.0,
        "match_type": "semantic",
        "left_excerpt": "采用分层架构解决跨部门数据孤岛",
        "right_excerpt": "通过多层体系消除部门之间的数据隔离",
        "left_location": {"content_type": "paragraph"},
        "right_location": {"content_type": "paragraph"},
        "image_comparison": None,
    }
    assert filter_candidate_payloads(semantic_rule, [semantic_candidate]) == [semantic_candidate]


def test_structured_and_ocr_signals_can_survive_low_text_evidence():
    table_rule = load_duplicate_rule(RULE_DIR / "D003 设备材料配置与技术参数.md")
    table_candidate = {
        "candidate_id": "table-target",
        "similarity_score": 0.92,
        "evidence_strength": 0.04,
        "lexical_score": 0.0,
        "structure_score": 0.82,
        "semantic_score": 0.0,
        "image_score": 0.0,
        "match_type": "structural",
        "left_excerpt": "ZX-9X | 12 | MARS-77",
        "right_excerpt": "ZX-9X | 12 | MARS-77",
        "left_location": {"content_type": "table_row"},
        "right_location": {"content_type": "table_row"},
        "table_comparison": {
            "numeric_signature_score": 1.0,
            "rare_cell_overlap": 0.9,
            "row_alignment_score": 0.8,
        },
    }
    assert filter_candidate_payloads(table_rule, [table_candidate]) == [table_candidate]

    ocr_rule = load_duplicate_rule(RULE_DIR / "D001 拟投入项目人员情况.md")
    ocr_candidate = {
        **table_candidate,
        "candidate_id": "ocr-target",
        "match_type": "ocr_error",
        "left_location": {"content_type": "image_ocr"},
        "right_location": {"content_type": "image_ocr"},
        "table_comparison": None,
    }
    assert filter_candidate_payloads(ocr_rule, [ocr_candidate]) == [ocr_candidate]


@pytest.mark.asyncio
@pytest.mark.parametrize("rule_id", EXPECTED_IDS)
async def test_each_rule_query_recalls_its_synthetic_target(tmp_path: Path, rule_id: str):
    rule = load_duplicate_rule(next(RULE_DIR.glob(f"{rule_id} *.md")))
    case = CASES[rule_id]
    left_path = tmp_path / f"{rule_id}-left.md"
    right_path = tmp_path / f"{rule_id}-right.md"
    content = f"# {rule.title}\n\n{case['target']}\n\n{case['noise']}"
    left_path.write_text(content, encoding="utf-8")
    right_path.write_text(content, encoding="utf-8")
    service = DuplicateCandidateService(
        DocumentDescriptor(id=f"{rule_id}-left", filename="A.md", path=str(left_path)),
        DocumentDescriptor(id=f"{rule_id}-right", filename="B.md", path=str(right_path)),
    )
    await service.build()
    retrieved = service.search(build_rule_query(rule), limit=50)
    selected = filter_candidate_payloads(rule, [item.to_agent_dict() for item in retrieved])
    assert selected, f"{rule_id} 未召回任何候选"
    assert any(case["target"].splitlines()[0][:16] in item["left_excerpt"] for item in selected)


@pytest.mark.asyncio
async def test_each_rule_query_prioritizes_its_domain_in_a_mixed_document(tmp_path: Path):
    rules = load_duplicate_rules(RULE_DIR)
    sections = []
    for rule in rules:
        case = CASES[rule.rule_id]
        sections.append(f"## {rule.rule_id} {rule.title}\n\n{case['target']}")
    content = "# 综合技术标\n\n" + "\n\n".join(sections)
    left_path = tmp_path / "mixed-left.md"
    right_path = tmp_path / "mixed-right.md"
    left_path.write_text(content, encoding="utf-8")
    right_path.write_text(content, encoding="utf-8")
    service = DuplicateCandidateService(
        DocumentDescriptor(id="mixed-left", filename="A.md", path=str(left_path)),
        DocumentDescriptor(id="mixed-right", filename="B.md", path=str(right_path)),
    )
    await service.build()

    for rule in rules:
        case = CASES[rule.rule_id]
        top_five = service.search(build_rule_query(rule), limit=5)
        selected = filter_candidate_payloads(rule, [item.to_agent_dict() for item in top_five])
        assert any(
            case["target"].splitlines()[0][:16] in item["left_excerpt"]
            for item in selected
        ), f"{rule.rule_id} 的目标候选未进入混合文档 Top-5"


def test_rule_query_is_compact_and_uses_declared_terms():
    rule = load_duplicate_rule(RULE_DIR / "D008 数据安全、系统集成与运维保障.md")
    query = build_rule_query(rule)
    assert len(query) <= 800
    assert all(term in query for term in rule.search_terms[:4])
    assert not re.search(r"```|输出要求|不得作出", query)


def test_agent_rejects_check_item_names_not_declared_by_rule():
    allowed = [{"id": "item-1", "title": "技术路线和总体流程"}]
    valid = DuplicateCheckAgent._parse_response(
        '[{"candidate_id":"c1","check_item_name":"技术路线和总体流程","verdict":"suspicious"}]',
        allowed_check_items=allowed,
    )
    assert valid[0]["check_item_name"] == "技术路线和总体流程"
    with pytest.raises(ValueError, match="check_item_name"):
        DuplicateCheckAgent._parse_response(
            '[{"candidate_id":"c1","check_item_name":"虚构检查项","verdict":"suspicious"}]',
            allowed_check_items=allowed,
        )
    with pytest.raises(ValueError, match="法律结论"):
        DuplicateCheckAgent._parse_response(
            '[{"candidate_id":"c1","check_item_name":"技术路线和总体流程",'
            '"verdict":"suspicious","explanation":"双方已经构成串标"}]',
            allowed_check_items=allowed,
        )


@pytest.mark.asyncio
async def test_agent_uses_rule_query_budgets_and_source_requirements():
    class EmptyCandidateService:
        def __init__(self):
            self.calls = []

        def search(self, query: str, *, limit: int):
            self.calls.append((query, limit))
            return []

    class EmptySourceIndex:
        def __init__(self):
            self.calls = []
            self.warnings = []

        def search(self, query: str, *, source_basis: str | None, limit: int):
            self.calls.append((query, source_basis, limit))
            return []

    candidate_service = EmptyCandidateService()
    source_index = EmptySourceIndex()
    path = RULE_DIR / "D009 企业资质、类似业绩、证书与知识产权.md"
    rule = load_duplicate_rule(path)
    agent = DuplicateCheckAgent(
        rule_doc_path=str(path),
        candidate_service=candidate_service,
        source_index=source_index,
        task_id="task",
        todo_id="todo",
        project_id="project",
        user_id="user",
        session_factory=None,
    )

    findings, check_items = await agent.run()

    assert findings == []
    assert candidate_service.calls == [(build_rule_query(rule), rule.max_candidates)]
    assert [call[1] for call in source_index.calls] == ["tender", "public"]
    assert sum(call[2] for call in source_index.calls) <= rule.source_candidates + 1
    assert len(check_items) == len(rule.check_items)


@pytest.mark.asyncio
@pytest.mark.parametrize("rule_id", EXPECTED_IDS)
async def test_each_rule_runs_through_agent_and_binds_tool_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rule_id: str,
):
    rule_path = next(RULE_DIR.glob(f"{rule_id} *.md"))
    rule = load_duplicate_rule(rule_path)
    case = CASES[rule_id]
    left_path = tmp_path / f"{rule_id}-agent-left.md"
    right_path = tmp_path / f"{rule_id}-agent-right.md"
    content = f"# {rule.title}\n\n{case['target']}\n\n{case['noise']}"
    left_path.write_text(content, encoding="utf-8")
    right_path.write_text(content, encoding="utf-8")
    service = DuplicateCandidateService(
        DocumentDescriptor(id=f"{rule_id}-left", filename="A.md", path=str(left_path)),
        DocumentDescriptor(id=f"{rule_id}-right", filename="B.md", path=str(right_path)),
    )
    await service.build()
    selected = filter_candidate_payloads(
        rule,
        [item.to_agent_dict() for item in service.search(build_rule_query(rule), limit=50)],
    )
    target = next(
        item
        for item in selected
        if case["target"].splitlines()[0][:16] in item["left_excerpt"]
    )

    class FakeClient:
        def __init__(self):
            self.messages = None

        async def generate(self, *, messages):
            self.messages = messages
            return SimpleNamespace(
                content=json.dumps(
                    [
                        {
                            "candidate_id": target["candidate_id"],
                            "check_item_name": rule.check_items[0]["title"],
                            "verdict": "suspicious",
                            "source_basis": "bidder_authored",
                            "match_type": target["match_type"],
                            "explanation": "合成场景中的双方自主内容包含相同罕见细节。",
                        }
                    ],
                    ensure_ascii=False,
                )
            )

    fake_client = FakeClient()

    async def fake_usage_context(_self):
        return None

    monkeypatch.setattr(DuplicateCheckAgent, "_set_usage_context", fake_usage_context)
    monkeypatch.setattr(duplicate_check_module, "create_llm_client", lambda **_kwargs: fake_client)
    monkeypatch.setattr(duplicate_check_module, "record_llm_usage", lambda **_kwargs: None)
    monkeypatch.setattr(duplicate_check_module, "reset_usage_context", lambda _token: None)
    events: list[tuple[str, dict]] = []
    agent = DuplicateCheckAgent(
        rule_doc_path=str(rule_path),
        candidate_service=service,
        source_index=None,
        task_id="task",
        todo_id=f"todo-{rule_id}",
        project_id="project",
        user_id="user",
        session_factory=None,
        event_callback=lambda event_type, data: events.append((event_type, data)),
    )

    findings, check_items = await agent.run()

    assert len(findings) == 1
    assert findings[0].evidence["candidate_id"] == target["candidate_id"]
    assert findings[0].left_excerpt == target["left_excerpt"]
    assert findings[0].similarity_score == target["similarity_score"]
    assert check_items[0]["title"] == rule.check_items[0]["title"]
    assert "rule_id:" not in fake_client.messages[1].content
    step = next(data for event, data in events if event == "sub_agent_step")
    tool_names = [item["name"] for item in step["tool_calls"]]
    assert "search_duplicate_candidates" in tool_names
    assert "filter_duplicate_candidates_by_rule" in tool_names
    filter_result = next(
        item["result"]
        for item in step["tool_results"]
        if item["name"] == "filter_duplicate_candidates_by_rule"
    )
    assert filter_result["selected_count"] >= 1
