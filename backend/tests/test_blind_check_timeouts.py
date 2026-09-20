"""ADR-0003: VSTO tool calls — adaptive timeouts, liveness, chunking, budgets."""

from __future__ import annotations

import itertools
from datetime import timedelta
from types import SimpleNamespace

import pytest

from backend.agent.blind_check_agent import (
    BlindCheckAgent,
    EVIDENCE_BUDGET_RATIO,
    SCAN_TIMEOUT_MAX_SECONDS,
    SCAN_TIMEOUT_MIN_SECONDS,
    document_characters,
    estimate_scan_timeout,
)
from backend.agent.tools.vsto_chunked import (
    MIN_CHUNK_PARAGRAPHS,
    PROBE_CHUNK_PARAGRAPHS,
    SINGLE_SHOT_MAX_PARAGRAPHS,
    ChunkedVstoTool,
)
from backend.agent.tools.vsto_remote import VstoRemoteTool
from backend.api.vsto_tools import submit_tool_progress, submit_tool_result
from backend.schemas.blind_check import VstoToolProgressRequest, VstoToolResultRequest
from backend.services import vsto_tool_broker as broker_module
from backend.services.vsto_tool_broker import (
    VSTO_TOOL_SCHEMAS,
    VstoToolBroker,
    _validate_tool_arguments,
)
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


def _pending_call(**overrides):
    values = {
        "call_id": "call-1",
        "status": "pending",
        "result": None,
        "error_message": None,
        "arguments": {"snapshot_id": "snapshot-1"},
        "answered_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _clock(*ticks: float):
    """monotonic() stub that keeps returning the last tick once exhausted."""
    iterator = itertools.chain(ticks, itertools.repeat(ticks[-1]))
    return lambda: next(iterator)


# ---------------------------------------------------------------------------
# schema: paragraph ranges
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_paragraph_range_arguments_only_on_scan_tools():
    for tool in ("word_check_text_style", "word_check_paragraph_format", "word_check_heading_numbering"):
        assert "paragraph_start" in VSTO_TOOL_SCHEMAS[tool]["properties"]
        _validate_tool_arguments(tool, {"snapshot_id": "s", "paragraph_start": 1, "paragraph_end": 800})
        with pytest.raises(ValueError, match="paragraph_start must not exceed"):
            _validate_tool_arguments(tool, {"paragraph_start": 10, "paragraph_end": 5})
        with pytest.raises(ValueError, match="paragraph_end"):
            _validate_tool_arguments(tool, {"paragraph_end": 0})
    with pytest.raises(ValueError, match="unexpected"):
        _validate_tool_arguments("word_check_objects", {"paragraph_start": 1})


# ---------------------------------------------------------------------------
# broker: per-call timeout, liveness, memo, circuit breaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_progress_heartbeat_extends_wait_beyond_initial_timeout(monkeypatch):
    task = _active_task()
    session = _active_session()
    call = _pending_call()
    # request(): task, session; loop 1: state poll; loop 2: consume hit -> snapshot + mark_finished
    db = _FakeDb([task, session, call, call, call])
    monkeypatch.setattr(broker_module, "_redis_client", lambda: None)
    monkeypatch.setattr(broker_module, "time", SimpleNamespace(monotonic=_clock(0.0, 1.0, 6.0)))
    consumed = iter([None, {"success": True, "data": {"snapshot_id": "snapshot-1", "ok": 1}, "content": "", "snapshot_id": "snapshot-1"}])
    monkeypatch.setattr(broker_module, "consume_tool_result", lambda _call_id: next(consumed))
    progress = iter([{"ts": 1.0, "checked_count": 400, "cursor": 400, "total": 5000}])
    monkeypatch.setattr(broker_module, "read_tool_progress", lambda _call_id: next(progress, None))
    events = []
    broker = VstoToolBroker(
        session_factory=_DbFactory(db),
        task_id="task-1",
        tool_session_id="session-1",
        timeout_seconds=5,
        event_callback=lambda event_type, data: events.append((event_type, data)),
    )
    result = await broker.request("word_check_text_style", {}, timeout_seconds=5)
    # 没有心跳时 t=6 已超过 5 s 期限；心跳把期限推到了绝对上限（2×5=10 s）内。
    assert result["success"] is True
    assert call.status == "completed"
    assert any(event[0] == "vsto_tool_progress" for event in events)
    assert not any(event[0] == "vsto_tool_timeout" for event in events)
    added = db.added[0]
    assert (added.expires_at - added.requested_at).total_seconds() == 10


@pytest.mark.asyncio
@pytest.mark.unit
async def test_timeout_emits_cancel_and_records_wasted_seconds(monkeypatch):
    task = _active_task()
    session = _active_session()
    call = _pending_call(call_id="call-timeout")
    db = _FakeDb([task, session, call, call])
    monkeypatch.setattr(broker_module, "_redis_client", lambda: None)
    monkeypatch.setattr(broker_module, "time", SimpleNamespace(monotonic=_clock(0.0, 6.0)))
    monkeypatch.setattr(broker_module, "consume_tool_result", lambda _call_id: None)
    events = []
    broker = VstoToolBroker(
        session_factory=_DbFactory(db),
        task_id="task-1",
        tool_session_id="session-1",
        timeout_seconds=5,
        event_callback=lambda event_type, data: events.append((event_type, data)),
    )
    result = await broker.request("word_get_overview", {})
    assert result["success"] is False and result["expired"] is True and result["call_id"]
    assert call.status == "expired"
    assert [name for name, _ in events if name in {"vsto_tool_timeout", "vsto_tool_cancel"}] == [
        "vsto_tool_timeout",
        "vsto_tool_cancel",
    ]
    assert broker.wasted_seconds == pytest.approx(6.0)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_request_event_carries_paired_server_timestamps_for_plugin_expiry(monkeypatch):
    """插件按 expires_at − requested_at（服务端 TTL）配合本机单调时钟判过期，两者必须成对下发。

    只发 expires_at 会让插件拿本机墙钟去比服务端时间：用户机器时钟快 3 分钟即把轻量工具
    （绝对期限 180 s）的全部请求在出队时误判为过期，暗标检查对该用户整体失效。
    """
    from datetime import datetime

    from backend.tasks.blind_check_tasks import _safe_progress_event

    task = _active_task()
    session = _active_session()
    call = _pending_call(call_id="call-stamps")
    db = _FakeDb([task, session, call, call])
    monkeypatch.setattr(broker_module, "_redis_client", lambda: None)
    monkeypatch.setattr(broker_module, "time", SimpleNamespace(monotonic=_clock(0.0, 6.0)))
    monkeypatch.setattr(broker_module, "consume_tool_result", lambda _call_id: None)
    events = []
    broker = VstoToolBroker(
        session_factory=_DbFactory(db),
        task_id="task-1",
        tool_session_id="session-1",
        timeout_seconds=5,
        event_callback=lambda event_type, data: events.append((event_type, data)),
    )
    await broker.request("word_get_overview", {})
    request_event = next(data for name, data in events if name == "vsto_tool_request")
    requested_at = datetime.fromisoformat(request_event["requested_at"])
    expires_at = datetime.fromisoformat(request_event["expires_at"])
    assert requested_at.tzinfo is not None and expires_at.tzinfo is not None
    assert (expires_at - requested_at).total_seconds() == 10  # 绝对上限 = 2 × timeout
    # SSE 白名单必须原样放行两枚时间戳，页面才能成对透传给插件。
    safe = _safe_progress_event("vsto_tool_request", request_event)
    assert safe["requested_at"] == request_event["requested_at"]
    assert safe["expires_at"] == request_event["expires_at"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_identical_call_is_served_from_memo_without_touching_word(monkeypatch):
    task = _active_task()
    session = _active_session()
    call = _pending_call()
    db = _FakeDb([task, session, call, call])  # exactly one real round trip
    monkeypatch.setattr(broker_module, "_redis_client", lambda: None)
    monkeypatch.setattr(broker_module, "time", SimpleNamespace(monotonic=_clock(0.0, 1.0)))
    monkeypatch.setattr(
        broker_module,
        "consume_tool_result",
        lambda _call_id: {"success": True, "data": {"snapshot_id": "snapshot-1"}, "content": "", "snapshot_id": "snapshot-1"},
    )
    events = []
    broker = VstoToolBroker(
        session_factory=_DbFactory(db),
        task_id="task-1",
        tool_session_id="session-1",
        event_callback=lambda event_type, data: events.append(event_type),
    )
    first = await broker.request("word_check_page_setup", {"check_a4": True})
    second = await broker.request("word_check_page_setup", {"check_a4": True, "snapshot_id": "snapshot-1"})
    assert first["success"] is True and second["success"] is True
    assert second["cached"] is True and "cached" not in first
    assert db.results == []  # 第二次没有再查库/再入队
    assert events.count("vsto_tool_request") == 1 and "vsto_tool_cached" in events


@pytest.mark.asyncio
@pytest.mark.unit
async def test_wasted_budget_opens_circuit_for_further_calls(monkeypatch):
    task = _active_task()
    session = _active_session()
    call = _pending_call(call_id="call-timeout")
    # 超时路径只读三次库：task、session、标记 expired；熔断后的调用不再碰库。
    db = _FakeDb([task, session, call])
    monkeypatch.setattr(broker_module, "_redis_client", lambda: None)
    monkeypatch.setattr(broker_module, "time", SimpleNamespace(monotonic=_clock(0.0, 6.0)))
    monkeypatch.setattr(broker_module, "consume_tool_result", lambda _call_id: None)
    events = []
    broker = VstoToolBroker(
        session_factory=_DbFactory(db),
        task_id="task-1",
        tool_session_id="session-1",
        timeout_seconds=5,
        wasted_budget_seconds=5,
        event_callback=lambda event_type, data: events.append(event_type),
    )
    await broker.request("word_get_overview", {})
    blocked = await broker.request("word_search", {"query": "公司"})
    assert blocked["success"] is False and blocked["circuit_open"] is True
    assert "vsto_tool_circuit_open" in events
    assert db.results == []


# ---------------------------------------------------------------------------
# chunked scans
# ---------------------------------------------------------------------------


class _FakeRemote:
    """Stands in for VstoRemoteTool: records ranges, answers per script."""

    def __init__(self, name="word_check_text_style", *, total=5000, legacy=False, fail_ranges=(), seconds=10.0):
        self.name = name
        self.description = "fake"
        self.default_timeout_seconds = None
        self.calls: list[dict] = []
        self.total = total
        self.legacy = legacy
        self.fail_ranges = set(fail_ranges)
        self.seconds = seconds
        self.broker = SimpleNamespace(fetch_late_result=self._no_late)

    @property
    def parameters(self):
        return {"type": "object", "properties": dict(VSTO_TOOL_SCHEMAS[self.name]["properties"]), "additionalProperties": False}

    async def _no_late(self, _call_id):
        return None

    async def request_raw(self, arguments, *, timeout_seconds=None, use_cache=True):
        args = dict(arguments)
        self.calls.append({"args": args, "timeout": timeout_seconds, "use_cache": use_cache})
        start, end = args.get("paragraph_start"), args.get("paragraph_end")
        if (start, end) in self.fail_ranges:
            return {"success": False, "data": {}, "content": "", "error": "VSTO 工具调用超时，文档可能已关闭或页面未连接", "expired": True, "call_id": "c"}
        if self.legacy or start is None:
            scope = {"kind": "whole_document", "paragraph_start": 1, "paragraph_end": self.total, "document_paragraph_count": self.total, "scope_complete": True}
            count = self.total
        else:
            scope = {"kind": "paragraph_range", "paragraph_start": start, "paragraph_end": end, "document_paragraph_count": self.total, "scope_complete": False}
            count = end - start + 1
        data = {
            "tool": self.name,
            "scope": scope,
            "coverage": "complete",
            "checked_count": count,
            "checked_paragraphs": count,
            "violation_count": 1,
            "violations": [{"rule_id": "text.size", "paragraph_index": start or 1, "story": "main"}],
            "unknown_reasons": [],
            "truncated": False,
            "expected": {"font": "宋体"},
            "snapshot_id": "snapshot-1",
        }
        return {"success": True, "data": data, "content": "", "error": None, "snapshot_id": "snapshot-1"}


@pytest.mark.unit
def test_chunked_tool_hides_range_arguments_from_the_model():
    tool = ChunkedVstoTool(inner=_FakeRemote())
    assert "paragraph_start" not in tool.parameters["properties"]
    assert "expected_font" in tool.parameters["properties"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_chunked_scan_merges_windows_and_adapts_window_size():
    remote = _FakeRemote(total=5000)
    # 每次调用“耗时” 10 s：探测窗 800 段 → 0.0125 s/段 → 目标 25 s ≈ 2000 段/窗
    tool = ChunkedVstoTool(inner=remote, clock=_clock(*[float(i) * 10 for i in range(40)]))
    tool.configure(total_paragraphs=5000)
    result = await tool.request_raw({"expected_font": "宋体", "snapshot_id": "snapshot-1"})
    ranges = [(c["args"]["paragraph_start"], c["args"]["paragraph_end"]) for c in remote.calls]
    assert ranges[0] == (1, PROBE_CHUNK_PARAGRAPHS)
    assert ranges[-1][1] == 5000
    assert all(ranges[i][1] + 1 == ranges[i + 1][0] for i in range(len(ranges) - 1))
    assert ranges[1][1] - ranges[1][0] + 1 == 2000
    data = result["data"]
    assert result["success"] is True
    assert data["coverage"] == "complete" and data["checked_count"] == 5000
    assert data["violation_count"] == len(ranges) and len(data["violations"]) == len(ranges)
    assert data["scope"]["kind"] == "whole_document" and data["scope"]["chunks"] == len(ranges)
    assert data["expected"] == {"font": "宋体"}
    assert all(c["use_cache"] is False for c in remote.calls)
    # 结果按参数记忆化：模型再调同参数不再触发任何窗口调用
    again = await tool.request_raw({"expected_font": "宋体"})
    assert again["cached"] is True and len(remote.calls) == len(ranges)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_chunked_scan_falls_back_to_single_call_for_small_documents():
    remote = _FakeRemote(total=SINGLE_SHOT_MAX_PARAGRAPHS)
    tool = ChunkedVstoTool(inner=remote)
    tool.configure(total_paragraphs=SINGLE_SHOT_MAX_PARAGRAPHS)
    result = await tool.request_raw({"expected_font": "宋体"})
    assert result["success"] is True
    assert len(remote.calls) == 1 and "paragraph_start" not in remote.calls[0]["args"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_chunked_scan_accepts_whole_document_echo_from_old_plugin():
    remote = _FakeRemote(total=9000, legacy=True)
    remote.default_timeout_seconds = 400
    tool = ChunkedVstoTool(inner=remote)
    tool.configure(total_paragraphs=9000)
    result = await tool.request_raw({"expected_font": "宋体"})
    assert result["success"] is True and result["data"]["checked_count"] == 9000
    assert len(remote.calls) == 1
    # 探测窗按整篇文档的自适应超时等待，旧插件扫全文也不会在分块超时上撞墙
    assert remote.calls[0]["timeout"] == 400
    assert tool.last_run["mode"] == "legacy_whole_document"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_chunked_scan_records_gap_instead_of_failing_dimension():
    # 第二个窗口 (801–2800) 超时，半窗重试 (801–1800) 也超时 → 记录未覆盖区间继续后面的窗口
    remote = _FakeRemote(total=5000, fail_ranges={(801, 2800), (801, 1800)})
    tool = ChunkedVstoTool(inner=remote, clock=_clock(*[float(i) * 10 for i in range(40)]))
    tool.configure(total_paragraphs=5000)
    result = await tool.request_raw({"expected_font": "宋体"})
    data = result["data"]
    assert result["success"] is True
    assert data["coverage"] == "partial"
    assert data["scope"]["uncovered_ranges"] == [[801, 1800]]
    assert any("801–1800" in reason for reason in data["unknown_reasons"])
    assert data["checked_count"] == 5000 - 1000
    ranges = [(c["args"]["paragraph_start"], c["args"]["paragraph_end"]) for c in remote.calls]
    assert ranges[2] == (801, 1800) and ranges[3][0] == 1801
    assert ranges[3][1] - ranges[3][0] + 1 == max(MIN_CHUNK_PARAGRAPHS, 1000)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_chunked_scan_aborts_when_probe_fails_twice():
    remote = _FakeRemote(total=5000, fail_ranges={(1, 800), (1, 400)})
    tool = ChunkedVstoTool(inner=remote)
    tool.configure(total_paragraphs=5000)
    result = await tool.request_raw({"expected_font": "宋体"})
    assert result["success"] is False and len(remote.calls) == 2
    assert tool.last_run["aborted"] is True


# ---------------------------------------------------------------------------
# agent: adaptive timeout and time budget
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_timeout_scales_with_document_size():
    assert estimate_scan_timeout(200, 8_000) == SCAN_TIMEOUT_MIN_SECONDS
    # 2026-09-17 事故文档：466 页 / 13,092 段 / 358,980 字符 → 固定 90 s 必超，估算约 400 s
    big = estimate_scan_timeout(13_092, 358_980)
    assert 350 <= big <= SCAN_TIMEOUT_MAX_SECONDS
    assert estimate_scan_timeout(100_000, 5_000_000) == SCAN_TIMEOUT_MAX_SECONDS
    assert document_characters({"document_revision": "2026/9/4 15:30:00|True|358980|13092|abc"}) == 358_980
    assert document_characters({"paragraph_count": 100}) == 4_000
    assert document_characters({}) is None


@pytest.mark.unit
def test_agent_configures_tools_from_overview():
    agent = object.__new__(BlindCheckAgent)
    remote = VstoRemoteTool(tool_name="word_check_objects", broker=SimpleNamespace())
    light = VstoRemoteTool(tool_name="word_check_page_setup", broker=SimpleNamespace())
    chunked = ChunkedVstoTool(inner=_FakeRemote())
    agent._remote_tools = {"word_check_objects": remote, "word_check_page_setup": light}
    agent._chunked_tools = {"word_check_text_style": chunked}
    profile = agent.configure_for_document(
        {"paragraph_count": 13_092, "document_revision": "2026/9/4 15:30:00|True|358980|13092|abc"}
    )
    assert profile["scan_timeout_seconds"] == remote.default_timeout_seconds >= 350
    assert light.default_timeout_seconds is None
    assert chunked.total_paragraphs == 13_092


@pytest.mark.asyncio
@pytest.mark.unit
async def test_heavy_tools_are_skipped_once_evidence_budget_is_spent():
    agent = object.__new__(BlindCheckAgent)
    agent.total_budget_seconds = 100
    agent._clock = _clock(0.0, 100 * EVIDENCE_BUDGET_RATIO + 1)
    agent._started_at = agent._clock()
    agent._tool_observations = []
    agent.budget_notes = []
    agent.tools = {"word_check_text_style": SimpleNamespace(execute=None)}
    observation = await agent._collect_tool("word_check_text_style", expected_font="宋体")
    assert observation["success"] is False and "预算" in observation["error"]
    assert agent.budget_notes and agent._tool_observations == [observation]


# ---------------------------------------------------------------------------
# api: late results and progress heartbeats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_late_result_is_accepted_while_task_still_runs(monkeypatch):
    session = _active_session()
    call = SimpleNamespace(
        call_id="call-late",
        session_id="session-1",
        task_id="task-1",
        tool_name="word_check_text_style",
        status="expired",
        expires_at=utc_now() - timedelta(minutes=1),
        arguments={"snapshot_id": "snapshot-1", "paragraph_start": 1, "paragraph_end": 800},
        result=None,
        error_message=None,
        answered_at=None,
    )
    db = _FakeDb([session, call, _active_task()])
    published = []
    monkeypatch.setattr("backend.api.vsto_tools.publish_tool_result", lambda call_id, result: published.append((call_id, result)))
    response = await submit_tool_result(
        VstoToolResultRequest(
            tool_session_id="session-1",
            call_id="call-late",
            success=True,
            data={"checked_count": 800},
            snapshot_id="snapshot-1",
        ),
        db,
        SimpleNamespace(id="user-1"),
    )
    assert response["status"] == "completed" and response["late"] is True
    assert call.status == "completed" and call.result["late"] is True
    assert published[0][1]["success"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_progress_heartbeat_updates_pending_call(monkeypatch):
    session = _active_session()
    call = SimpleNamespace(
        call_id="call-1",
        session_id="session-1",
        task_id="task-1",
        status="pending",
        last_progress_at=None,
        progress_cursor=None,
    )
    db = _FakeDb([session, call, _active_task()])
    published = []
    monkeypatch.setattr("backend.api.vsto_tools.publish_tool_progress", lambda call_id, payload: published.append((call_id, payload)))
    response = await submit_tool_progress(
        VstoToolProgressRequest(tool_session_id="session-1", call_id="call-1", checked_count=1200, cursor=1200, total=13092),
        db,
        SimpleNamespace(id="user-1"),
    )
    assert response["ignored"] is False
    assert call.last_progress_at is not None and call.progress_cursor == 1200
    assert published[0][1]["cursor"] == 1200 and published[0][1]["total"] == 13092

    settled = SimpleNamespace(call_id="call-2", session_id="session-1", task_id="task-1", status="completed")
    db2 = _FakeDb([_active_session(), settled, _active_task()])
    response = await submit_tool_progress(
        VstoToolProgressRequest(tool_session_id="session-1", call_id="call-2", checked_count=1),
        db2,
        SimpleNamespace(id="user-1"),
    )
    assert response["ignored"] is True and db2.commits == 0
