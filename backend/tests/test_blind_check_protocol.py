"""Focused unit tests for the blind-check/VSTO protocol boundary."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from backend.agent.blind_check_agent import (
    BlindCheckAgent,
    _apply_echo_verification,
    _build_coverage_report,
    _build_deterministic_arguments,
    _coverage_unknown_findings,
    _legacy_story_note,
    _normalize_evidences,
    _normalize_findings,
    _parse_agent_json,
    _parse_last_json_object,
    _select_deterministic_tools,
    _summarize,
    _uncovered_dimension_findings,
    _uncovered_tool_dimensions,
)
from backend.agent.tools.vsto_remote import VstoRemoteTool
from backend.api.vsto_tools import _session, submit_tool_result
from backend.schemas.blind_check import BlindCheckScope, VstoToolResultRequest
from backend.services import vsto_tool_broker as broker_module
from backend.services.vsto_tool_broker import (
    VSTO_TOOL_NAMES,
    VstoToolBroker,
    _validate_tool_arguments,
    consume_tool_result,
    publish_tool_result,
)
from backend.tasks.blind_check_tasks import _safe_progress_event
from backend.utils.time_utils import utc_now


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDb:
    def __init__(self, results):
        self.results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _statement):
        value = self.results.pop(0)
        return _ScalarResult(value)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


class _DbFactory:
    def __init__(self, db):
        self.db = db

    def __call__(self):
        factory = self

        class _Context:
            async def __aenter__(self):
                return factory.db

            async def __aexit__(self, *_args):
                return False

        return _Context()


def _active_session(**overrides):
    values = {
        "id": "session-1",
        "user_id": "user-1",
        "status": "active",
        "expires_at": utc_now() + timedelta(minutes=10),
        "snapshot_id": "snapshot-1",
        "document_key": "doc-1",
        "document_revision": "revision-1",
        "last_seen_at": utc_now(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _active_task(**overrides):
    values = {
        "id": "task-1",
        "user_id": "user-1",
        "tool_session_id": "session-1",
        "status": "running",
        "snapshot_id": "snapshot-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.unit
def test_vsto_tool_registry_and_argument_schema_are_closed():
    assert VSTO_TOOL_NAMES == {
        "word_get_overview",
        "word_search",
        "word_check_format",
        "word_scan_identity_clues",
        "word_check_page_setup",
        "word_check_headers_footers",
        "word_check_blank_pages",
        "word_check_text_style",
        "word_check_paragraph_format",
        "word_check_heading_numbering",
        "word_check_objects",
        "word_check_signatures",
    }
    _validate_tool_arguments(
        "word_search",
        {"query": "公司", "max_results": 5, "snapshot_id": "snapshot-1"},
    )
    with pytest.raises(ValueError, match="unexpected"):
        _validate_tool_arguments("word_search", {"query": "公司", "write": True})
    with pytest.raises(ValueError, match="max_results"):
        _validate_tool_arguments("word_search", {"query": "公司", "max_results": 0})
    with pytest.raises(ValueError, match="snapshot_id"):
        _validate_tool_arguments("word_get_overview", {"snapshot_id": ""})
    _validate_tool_arguments(
        "word_check_format",
        {"requirements": "暗" * 50_000, "snapshot_id": "snapshot-1"},
    )
    _validate_tool_arguments(
        "word_check_text_style",
        {
            "snapshot_id": "snapshot-1",
            "expected_font": "宋体",
            "expected_size_pt": 14,
            "expected_color_rgb": [0, 0, 0],
        },
    )
    _validate_tool_arguments(
        "word_check_heading_numbering",
        {"snapshot_id": "snapshot-1", "max_level": 7, "formats": ["^一、"]},
    )
    with pytest.raises(ValueError, match="expected_color_rgb"):
        _validate_tool_arguments(
            "word_check_text_style",
            {"expected_color_rgb": [0, 0, 256]},
        )


@pytest.mark.unit
def test_selection_scope_is_not_supported():
    with pytest.raises(ValueError, match="whole_document"):
        BlindCheckScope(mode="selection", confirmed=True)


@pytest.mark.unit
def test_whole_document_scope_requires_explicit_confirmation():
    with pytest.raises(ValueError, match="用户确认"):
        BlindCheckScope(mode="whole_document")

    scope = BlindCheckScope(mode="whole_document", confirmed=True)
    assert scope.confirmed is True


@pytest.mark.unit
def test_tool_result_local_fallback_is_consumed_once(monkeypatch):
    monkeypatch.setattr(broker_module, "_redis_client", lambda: None)
    broker_module._LOCAL_RESULTS.clear()
    payload = {"success": True, "data": {"snapshot_id": "snapshot-1"}}
    publish_tool_result("call-1", payload)
    assert consume_tool_result("call-1") == payload
    assert consume_tool_result("call-1") is None


@pytest.mark.unit
def test_agent_json_is_normalized_and_unknown_is_not_pass(monkeypatch):
    parsed = _parse_agent_json("```json\n{\"findings\": [{\"verdict\": \"compliant\"}]}\n```")
    assert parsed and parsed["findings"]
    findings = _normalize_findings(
        [
            {"category": "not-a-category", "verdict": "not-a-verdict"},
            {"verdict": "violation", "severity": "major", "title": "名称"},
        ]
    )
    summary = _summarize(findings)
    assert summary["overall"] == "fail"
    assert summary["major"] == 1
    assert _summarize(_normalize_findings([{ "verdict": "unknown" }]))["overall"] == "unknown"
    nested = _parse_agent_json(
        '{"summary": {"overall": "pass"}, "findings": [{"location": {"query": "名称"}}]}'
    )
    assert nested and "findings" in nested
    overview = _parse_last_json_object(
        '{"document_name":"bid.docx","snapshot_id":"snap-1","metadata":{"author":"A"}}'
    )
    assert overview and overview["snapshot_id"] == "snap-1"


@pytest.mark.unit
def test_incomplete_deterministic_coverage_forces_unknown():
    coverage = _build_coverage_report(
        [
            {
                "tool": "word_check_text_style",
                "success": True,
                "data": {
                    "coverage": "partial",
                    "checked_count": 10,
                    "violation_count": 0,
                    "unknown_reasons": ["只扫描了部分字符"],
                },
            }
        ]
    )
    findings = _coverage_unknown_findings(coverage)
    assert findings and findings[0]["verdict"] == "unknown"
    summary = _summarize(findings, coverage=coverage)
    assert summary["overall"] == "unknown"
    assert summary["coverage_complete"] is False


@pytest.mark.unit
def test_uncovered_tool_dimensions_degrade_to_one_unknown_per_tool():
    """ADR-0002：raw 违规不再物化；未覆盖维度每工具一条待确认降级卡。"""
    observations = [
        {
            "tool": "word_check_paragraph_format",
            "data": {
                "coverage": "complete",
                "violations": [
                    {
                        "rule_id": "paragraph.line_spacing_rule",
                        "severity": "major",
                        "title": "行距规则不是固定值",
                        "description": "期望 exactly，实际 rule=0",
                        "evidence_text": "施",
                        "page_number": 1,
                        "paragraph_index": 1,
                    },
                    {
                        "rule_id": "paragraph.line_spacing",
                        "severity": "major",
                        "title": "行距不是要求值",
                        "description": "期望 25 磅，实际 12 磅",
                        "evidence_text": "工",
                        "page_number": 1,
                        "paragraph_index": 2,
                    },
                ],
            },
        }
    ]
    # 模型完全没覆盖该维度 → 守门员判定未覆盖
    assert _uncovered_tool_dimensions([], observations)
    findings = _uncovered_dimension_findings([], observations)
    assert len(findings) == 1
    assert findings[0]["verdict"] == "unknown"
    assert "段落格式" in findings[0]["title"]
    assert "2 处" in findings[0]["description"]

    # 模型已声明覆盖（rule_references）→ 不再降级
    covered = [
        {
            "verdict": "violation",
            "title": "行距不符合固定值 25 磅",
            "rule_references": ["paragraph.line_spacing_rule", "paragraph.line_spacing"],
        }
    ]
    assert _uncovered_tool_dimensions(covered, observations) == []
    assert _uncovered_dimension_findings(covered, observations) == []

    # 只覆盖部分规则 → 仅剩未覆盖规则参与降级
    partial = [{"verdict": "violation", "title": "x", "rule_references": ["paragraph.line_spacing"]}]
    rest = _uncovered_tool_dimensions(partial, observations)
    assert len(rest) == 1
    assert all(
        item["rule_id"] == "paragraph.line_spacing_rule" for item in rest[0]["violations"]
    )


@pytest.mark.unit
def test_normalize_evidences_gates_locateable_on_story_and_text():
    evidences = _normalize_evidences(
        [
            # 正文原文 + 工具标记可定位 → 保留 locateable
            {"text": "主要施工方案与技术措施", "page_number": 2, "paragraph_index": 7, "story": "main", "locateable": True},
            # 页眉页脚故事：即使模型标了 true 也强制不可定位
            {"text": "1", "story": "footer", "locateable": True},
            # 空文本不可定位
            {"text": "   ", "story": "main", "locateable": True},
            # 无 story（旧插件/模型省略）且有正文文本 → 允许定位（与旧行为一致）
            {"text": "施", "page_number": 1, "paragraph_index": 1, "locateable": True},
            # 未标记 locateable → false
            {"text": "作者：张三", "story": None},
        ]
    )
    assert [item["locateable"] for item in evidences] == [True, False, False, True, False]
    # 派生旧字段：normalize 后首条可定位证据填 location.query
    finding = _normalize_findings(
        [{"verdict": "violation", "evidences": evidences}]
    )[0]
    assert finding["location"] == {"query": "主要施工方案与技术措施"}
    assert finding["evidence_text"] == "主要施工方案与技术措施"


@pytest.mark.unit
def test_legacy_story_note_only_for_untagged_format_tools():
    untagged = [
        {
            "tool": "word_check_text_style",
            "data": {"violations": [{"rule_id": "text.font", "evidence_text": ""}]},
        }
    ]
    assert _legacy_story_note(untagged) is not None
    tagged = [
        {
            "tool": "word_check_text_style",
            "data": {"violations": [{"rule_id": "text.font", "story": "main"}]},
        }
    ]
    assert _legacy_story_note(tagged) is None
    assert _legacy_story_note([]) is None


@pytest.mark.unit
def test_requirement_selects_relevant_deterministic_tools():
    selected = _select_deterministic_tools("A4，宋体四号，固定值28磅，无页眉页脚，不得插入图片，不得电子签章")
    assert "word_check_page_setup" in selected
    assert "word_check_text_style" in selected
    assert "word_check_paragraph_format" in selected
    assert "word_check_headers_footers" in selected
    assert "word_check_objects" in selected
    assert "word_check_signatures" in selected


# 2026-09-12 生产事故回归样本：用户粘贴的行距/页边距/编号口径与样例要求不同时，
# 确定性工具曾按硬编码默认值（28 磅、统一 2.5 厘米、“（一）”格式）判定，产生大量假阳性。
_PROD_REQUIREMENT = (
    "技术标正文应采用“白底A4 纸张，纵向排版”，“宋体”四号字(黑色)；图表中字体和字号自拟；"
    "不得设置页码、页眉、页脚；对齐方式：左对齐；大纲级别：正文文本；行距为固定值25 磅；"
    "段前：0 行/0 磅，段后：0 行/0 磅；左侧缩进：0 字符/0 厘米，右侧缩进：0字符/0 厘米；"
    "页边距（上3 厘米，下3 厘米，左2 厘米，右2 厘米）。"
)

_SAMPLE_REQUIREMENT = (
    "（2）排版要求：全文采用A4大小，不允许插入空白页，页边距均为2.5厘米，不得出现页眉、页脚、页码，"
    "全文均为白底黑字，字体为宋体四号字，行间距采用固定值28磅，段前段后间距为0。"
    "（3）标题编号要求：标题序号最多设置7级，一级为“一、”，二级为“（一）”，三级为“1.”，"
    "四级为“（1）”，五级为“1）”，六级为“a.”，七级为“a）”。"
)


@pytest.mark.unit
def test_requirement_parsing_uses_pasted_values_not_defaults():
    arguments, notes = _build_deterministic_arguments(_PROD_REQUIREMENT)
    assert notes == []
    assert arguments["word_check_paragraph_format"]["line_spacing_pt"] == 25.0
    assert arguments["word_check_paragraph_format"]["check_space"] is True
    assert arguments["word_check_page_setup"] == {
        "margin_top_cm": 3.0,
        "margin_bottom_cm": 3.0,
        "margin_left_cm": 2.0,
        "margin_right_cm": 2.0,
    }
    # “大纲级别：正文文本”必须映射为 body_text_outline 口径，而不是默认的标题样式要求。
    assert arguments["word_check_heading_numbering"]["heading_policy"] == "body_text_outline"
    assert arguments["word_check_heading_numbering"]["check_number_format"] is False
    assert "formats" not in arguments["word_check_heading_numbering"]
    assert arguments["word_check_text_style"]["expected_size_pt"] == 14.0
    assert arguments["word_check_text_style"]["expected_font"] == "宋体"


@pytest.mark.unit
def test_requirement_parsing_handles_sample_requirement_and_max_level():
    arguments, notes = _build_deterministic_arguments(_SAMPLE_REQUIREMENT)
    assert notes == []
    assert arguments["word_check_paragraph_format"]["line_spacing_pt"] == 28.0
    assert arguments["word_check_paragraph_format"]["line_spacing_rule"] == "exactly"
    assert arguments["word_check_page_setup"] == {"margin_cm": 2.5}
    heading = arguments["word_check_heading_numbering"]
    assert heading["heading_policy"] == "require_heading_styles"
    assert heading["max_level"] == 7
    formats = heading["formats"]
    assert formats[0] == "^[一二三四五六七八九十百千万零〇]+、"
    assert formats[1] == "^（[一二三四五六七八九十百千万零〇]+）"
    assert formats[2] == "^\\d+\\."


@pytest.mark.unit
def test_requirement_parsing_reports_unresolvable_dimensions():
    arguments, notes = _build_deterministic_arguments("行距要符合要求，页边距按招标文件，字体另行规定")
    assert arguments["word_check_paragraph_format"] == {
        "check_line_spacing": True,
        "check_space": False,
    }
    assert arguments["word_check_page_setup"] == {"check_margins": False}
    assert any("行距" in note for note in notes)
    assert any("页边距" in note for note in notes)
    assert any("字体" in note for note in notes)


def _observation(expected: dict, violations: list[dict]) -> dict:
    return {
        "tool": "tool",
        "success": True,
        "error": None,
        "content": "",
        "data": {
            "coverage": "complete",
            "checked_count": len(violations),
            "violation_count": len(violations),
            "violations": violations,
            "unknown_reasons": [],
            "expected": expected,
        },
    }


@pytest.mark.unit
def test_echo_verification_drops_old_plugin_default_value_violations():
    arguments, _ = _build_deterministic_arguments(_PROD_REQUIREMENT)
    paragraph = _observation(
        # 旧插件忽略 line_spacing_pt=25，按内置默认 28 磅比较。
        expected={
            "line_spacing_rule": "exactly",
            "line_spacing_pt": 28,
            "space_before_pt": 0,
            "space_after_pt": 0,
        },
        violations=[
            {"rule_id": "paragraph.line_spacing", "title": "行距不是要求值", "severity": "major"},
            {"rule_id": "paragraph.line_spacing_rule", "title": "行距规则不是固定值", "severity": "major"},
        ],
    )
    _apply_echo_verification(paragraph, "word_check_paragraph_format", arguments["word_check_paragraph_format"])
    kept = [item["rule_id"] for item in paragraph["data"]["violations"]]
    # 行距数值按错误参数计算被丢弃；行距规则参数一致，违规保留。
    assert kept == ["paragraph.line_spacing_rule"]
    assert paragraph["data"]["violation_count"] == 1
    assert any("行距数值" in reason for reason in paragraph["data"]["unknown_reasons"])

    page = _observation(
        # 旧插件不认识四边页边距参数，按统一 2.5 厘米比较。
        expected={"page_size": "A4", "margin_cm": 2.5},
        violations=[{"rule_id": "page.margins", "title": "页边距不是统一的 2.5 厘米", "severity": "major"}],
    )
    _apply_echo_verification(page, "word_check_page_setup", arguments["word_check_page_setup"])
    assert page["data"]["violations"] == []
    assert any("页边距" in reason for reason in page["data"]["unknown_reasons"])

    heading = _observation(
        # 旧插件没有 heading_policy 概念，按“必须用标题样式+（一）格式”判定。
        expected={"max_level": 7, "formats": ["^[一", "^（一", "^1"]},
        violations=[
            {"rule_id": "heading.number_format", "title": "标题编号格式不符合要求", "severity": "major"},
            {"rule_id": "heading.max_level", "title": "标题级别超过限制", "severity": "major"},
        ],
    )
    _apply_echo_verification(heading, "word_check_heading_numbering", arguments["word_check_heading_numbering"])
    assert heading["data"]["violations"] == []


@pytest.mark.unit
def test_echo_verification_keeps_violations_when_plugin_echoes_intent():
    arguments, _ = _build_deterministic_arguments(_PROD_REQUIREMENT)
    paragraph = _observation(
        expected={
            "check_line_spacing": True,
            "line_spacing_rule": "exactly",
            "line_spacing_pt": 25.0,
            "check_space": True,
            "space_before_pt": 0.0,
            "space_after_pt": 0.0,
        },
        violations=[
            {"rule_id": "paragraph.line_spacing", "title": "行距不是要求值", "severity": "major"},
        ],
    )
    _apply_echo_verification(paragraph, "word_check_paragraph_format", arguments["word_check_paragraph_format"])
    assert [item["rule_id"] for item in paragraph["data"]["violations"]] == ["paragraph.line_spacing"]
    assert paragraph["data"]["unknown_reasons"] == []

    heading = _observation(
        expected={
            "heading_policy": "body_text_outline",
            "check_number_format": False,
            "max_level": None,
            "formats": None,
        },
        violations=[
            {"rule_id": "heading.outline_level", "title": "大纲级别不是正文文本", "severity": "major"},
        ],
    )
    _apply_echo_verification(heading, "word_check_heading_numbering", arguments["word_check_heading_numbering"])
    assert [item["rule_id"] for item in heading["data"]["violations"]] == ["heading.outline_level"]


@pytest.mark.unit
def test_broker_schema_accepts_new_per_side_and_policy_arguments():
    _validate_tool_arguments(
        "word_check_page_setup",
        {
            "snapshot_id": "snapshot-1",
            "margin_top_cm": 3,
            "margin_bottom_cm": 3,
            "margin_left_cm": 2,
            "margin_right_cm": 2,
        },
    )
    _validate_tool_arguments(
        "word_check_paragraph_format",
        {"snapshot_id": "snapshot-1", "check_line_spacing": True, "check_space": False},
    )
    _validate_tool_arguments(
        "word_check_heading_numbering",
        {
            "snapshot_id": "snapshot-1",
            "heading_policy": "body_text_outline",
            "check_number_format": False,
            "formats": ["^[一]+、", "", ""],
        },
    )
    with pytest.raises(ValueError, match="heading_policy"):
        _validate_tool_arguments(
            "word_check_heading_numbering",
            {"snapshot_id": "snapshot-1", "heading_policy": "bogus"},
        )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_stops_with_unknown_when_overview_cannot_be_read(monkeypatch):
    agent = object.__new__(BlindCheckAgent)

    async def failed_overview():
        return {"success": False, "error": "Word 文档已关闭"}

    monkeypatch.setattr(agent, "collect_overview", failed_overview)
    result = await agent.run_blind_check()
    assert result["summary"]["overall"] == "unknown"
    assert result["findings"][0]["verdict"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_expired_vsto_session_is_rejected_before_call_creation():
    expired = _active_session(expires_at=utc_now() - timedelta(seconds=1))
    db = _FakeDb([expired])
    with pytest.raises(Exception) as exc_info:
        await _session("session-1", SimpleNamespace(id="user-1"), db)
    assert getattr(exc_info.value, "status_code", None) == 409


@pytest.mark.asyncio
@pytest.mark.unit
async def test_duplicate_tool_result_is_idempotent():
    session = _active_session()
    call = SimpleNamespace(
        call_id="call-1",
        session_id="session-1",
        task_id="task-1",
        tool_name="word_get_overview",
        status="completed",
    )
    db = _FakeDb([session, call, _active_task()])
    response = await submit_tool_result(
        VstoToolResultRequest(
            tool_session_id="session-1",
            call_id="call-1",
            success=True,
            data={"snapshot_id": "snapshot-1"},
            snapshot_id="snapshot-1",
        ),
        db,
        SimpleNamespace(id="user-1"),
    )
    assert response["idempotent"] is True
    assert db.commits == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_late_tool_result_is_rejected_after_task_finishes(monkeypatch):
    session = _active_session()
    call = SimpleNamespace(
        call_id="call-late",
        session_id="session-1",
        task_id="task-1",
        tool_name="word_get_overview",
        status="pending",
        result=None,
        error_message=None,
        answered_at=None,
    )
    task = _active_task(status="cancelled")
    db = _FakeDb([session, call, task])
    published = []
    monkeypatch.setattr(
        "backend.api.vsto_tools.publish_tool_result",
        lambda call_id, result: published.append((call_id, result)),
    )
    with pytest.raises(Exception) as exc_info:
        await submit_tool_result(
            VstoToolResultRequest(
                tool_session_id="session-1",
                call_id="call-late",
                success=True,
                data={"snapshot_id": "snapshot-1"},
                snapshot_id="snapshot-1",
            ),
            db,
            SimpleNamespace(id="user-1"),
        )
    assert getattr(exc_info.value, "status_code", None) == 409
    assert call.status == "failed"
    assert published and published[0][1]["success"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_result_from_a_new_document_snapshot_is_downgraded_to_failure(monkeypatch):
    session = _active_session()
    call = SimpleNamespace(
        call_id="call-1",
        session_id="session-1",
        task_id="task-1",
        tool_name="word_get_overview",
        status="pending",
        expires_at=utc_now() + timedelta(minutes=1),
        arguments={"snapshot_id": "snapshot-1"},
        result=None,
        error_message=None,
        answered_at=None,
    )
    task = _active_task()
    db = _FakeDb([session, call, task])
    published = []
    monkeypatch.setattr(
        "backend.api.vsto_tools.publish_tool_result",
        lambda call_id, result: published.append((call_id, result)),
    )
    response = await submit_tool_result(
        VstoToolResultRequest(
            tool_session_id="session-1",
            call_id="call-1",
            success=True,
            data={"document_name": "changed.docx"},
            snapshot_id="snapshot-2",
        ),
        db,
        SimpleNamespace(id="user-1"),
    )
    assert response["status"] == "failed"
    assert call.status == "failed"
    assert "快照已变化" in call.error_message
    assert published and published[0][1]["success"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_broker_timeout_marks_call_expired(monkeypatch):
    task = _active_task()
    session = _active_session()
    call = SimpleNamespace(
        call_id="call-timeout",
        status="pending",
        result=None,
        error_message=None,
        arguments={"snapshot_id": "snapshot-1"},
        answered_at=None,
    )
    db = _FakeDb([task, session, call, call])
    factory = _DbFactory(db)
    ticks = iter([0.0, 6.0])
    monkeypatch.setattr(
        broker_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks)),
    )
    monkeypatch.setattr(broker_module, "consume_tool_result", lambda _call_id: None)
    events = []
    broker = VstoToolBroker(
        session_factory=factory,
        task_id="task-1",
        tool_session_id="session-1",
        timeout_seconds=5,
        event_callback=lambda event_type, data: events.append((event_type, data)),
    )
    result = await broker.request("word_get_overview", {})
    assert result["success"] is False
    assert call.status == "expired"
    assert any(event[0] == "vsto_tool_timeout" for event in events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_remote_tool_does_not_duplicate_equivalent_json_content():
    class _Broker:
        async def request(self, _tool_name, _arguments):
            return {
                "success": True,
                "data": {"snapshot_id": "snapshot-1", "page_count": 3},
                "content": '{"snapshot_id":"snapshot-1","page_count":3}',
            }

    tool = VstoRemoteTool(tool_name="word_get_overview", broker=_Broker())
    result = await tool.execute(snapshot_id="snapshot-1")
    assert result.success is True
    assert result.content.count("snapshot_id") == 1


@pytest.mark.unit
def test_progress_events_redact_model_and_tool_result_contents():
    llm_event = _safe_progress_event(
        "llm_output",
        {
            "step": 2,
            "thinking": "敏感推理",
            "content": "敏感证据",
            "tool_calls": [{"name": "word_search", "arguments": {"query": "公司"}}],
        },
    )
    assert "敏感推理" not in str(llm_event)
    assert "敏感证据" not in str(llm_event)
    assert llm_event["tools"] == ["word_search"]

    result_event = _safe_progress_event(
        "vsto_tool_result",
        {"call_id": "call-1", "success": True, "tool_result": {"content": "原文"}},
    )
    assert result_event == {"call_id": "call-1", "success": True}

    request_event = _safe_progress_event(
        "vsto_tool_request",
        {"call_id": "call-1", "arguments": {"query": "公司", "requirements": "暗" * 10_000}},
    )
    assert request_event["arguments"]["query"] == "公司"
    assert len(request_event["arguments"]["requirements"]) == 10_000
