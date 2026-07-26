"""Focused unit tests for the blind-check/VSTO protocol boundary."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from backend.agent.blind_check_agent import (
    BlindCheckAgent,
    _build_coverage_report,
    _coverage_unknown_findings,
    _deterministic_findings,
    _normalize_findings,
    _parse_agent_json,
    _parse_last_json_object,
    _select_deterministic_tools,
    _summarize,
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
def test_deterministic_violation_is_materialized_even_without_model_finding():
    findings = _deterministic_findings(
        [
            {
                "tool": "word_check_page_setup",
                "data": {
                    "coverage": "complete",
                    "violations": [
                        {
                            "rule_id": "page.a4",
                            "severity": "major",
                            "title": "页面不是 A4 尺寸",
                            "description": "第 2 节不是 A4",
                            "evidence_text": "section 2",
                            "page_number": 3,
                        }
                    ],
                },
            }
        ]
    )
    assert findings[0]["verdict"] == "violation"
    assert findings[0]["confidence"] == 1.0


@pytest.mark.unit
def test_requirement_selects_relevant_deterministic_tools():
    selected = _select_deterministic_tools("A4，宋体四号，固定值28磅，无页眉页脚，不得插入图片，不得电子签章")
    assert "word_check_page_setup" in selected
    assert "word_check_text_style" in selected
    assert "word_check_paragraph_format" in selected
    assert "word_check_headers_footers" in selected
    assert "word_check_objects" in selected
    assert "word_check_signatures" in selected


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
