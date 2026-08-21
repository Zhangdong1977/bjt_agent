"""Tests for MasterAgent rule-doc filtering (user-selected check categories)."""

import json
from types import SimpleNamespace

import pytest

from backend.agent.master.master_agent import MasterAgent


class FakeTodoService:
    def __init__(self):
        self.created = []

    async def create_todo(self, **kwargs):
        self.created.append(kwargs["rule_doc_name"])
        return SimpleNamespace(
            id=kwargs["rule_doc_name"],
            project_id=kwargs["project_id"],
            rule_doc_path=kwargs["rule_doc_path"],
            rule_doc_name=kwargs["rule_doc_name"],
        )


def _make_agent(**kwargs):
    return MasterAgent(
        project_id="project_1",
        rule_library_path="/rules",
        tender_docs=[("tender.md", "/tmp/tender.md")],
        bid_docs=[("bid.md", "/tmp/bid.md")],
        user_id="user_1",
        **kwargs,
    )


def _fake_scan_with(names):
    content = json.dumps({
        "rule_docs": [
            {"name": n, "path": f"/rules/{n}", "stem": n[:-3]} for n in names
        ]
    })

    async def fake_scan(_path):
        return SimpleNamespace(success=True, content=content)

    return fake_scan


@pytest.mark.asyncio
async def test_filter_creates_todos_only_for_selected_docs(monkeypatch):
    agent = _make_agent(rule_doc_filter=["r2.md"])
    monkeypatch.setattr(agent.scanner, "execute", _fake_scan_with(["r1.md", "r2.md", "r3.md"]))

    async def fake_run_sub_agents(_todo_service, _cancel_event=None):
        return {"total": 1, "completed": 1, "failed": 0, "cancelled": 0,
                "max_retries_exceeded": 0, "exceptions": 0}

    async def fake_aggregate(_todo_service):
        return {"total_findings": 0}

    monkeypatch.setattr(agent, "_run_sub_agents", fake_run_sub_agents)
    monkeypatch.setattr(agent, "_simple_aggregate", fake_aggregate)

    todo_service = FakeTodoService()
    result = await agent.run(todo_service, session_id="task_1")

    assert result["success"] is True
    assert todo_service.created == ["r2.md"]


@pytest.mark.asyncio
async def test_filter_without_selection_runs_all_docs(monkeypatch):
    agent = _make_agent()
    monkeypatch.setattr(agent.scanner, "execute", _fake_scan_with(["r1.md", "r2.md"]))

    async def fake_run_sub_agents(_todo_service, _cancel_event=None):
        return {"total": 2, "completed": 2, "failed": 0, "cancelled": 0,
                "max_retries_exceeded": 0, "exceptions": 0}

    async def fake_aggregate(_todo_service):
        return {"total_findings": 0}

    monkeypatch.setattr(agent, "_run_sub_agents", fake_run_sub_agents)
    monkeypatch.setattr(agent, "_simple_aggregate", fake_aggregate)

    todo_service = FakeTodoService()
    result = await agent.run(todo_service, session_id="task_1")

    assert result["success"] is True
    assert todo_service.created == ["r1.md", "r2.md"]


@pytest.mark.asyncio
async def test_filter_with_unknown_names_ignores_them(monkeypatch):
    agent = _make_agent(rule_doc_filter=["r1.md", "gone.md"])
    monkeypatch.setattr(agent.scanner, "execute", _fake_scan_with(["r1.md", "r2.md"]))

    async def fake_run_sub_agents(_todo_service, _cancel_event=None):
        return {"total": 1, "completed": 1, "failed": 0, "cancelled": 0,
                "max_retries_exceeded": 0, "exceptions": 0}

    async def fake_aggregate(_todo_service):
        return {"total_findings": 0}

    monkeypatch.setattr(agent, "_run_sub_agents", fake_run_sub_agents)
    monkeypatch.setattr(agent, "_simple_aggregate", fake_aggregate)

    todo_service = FakeTodoService()
    result = await agent.run(todo_service, session_id="task_1")

    assert result["success"] is True
    assert todo_service.created == ["r1.md"]


@pytest.mark.asyncio
async def test_filter_matching_nothing_fails_with_clear_error(monkeypatch):
    agent = _make_agent(rule_doc_filter=["nope.md"])
    monkeypatch.setattr(agent.scanner, "execute", _fake_scan_with(["r1.md", "r2.md"]))

    async def fail_if_sub_agents_called(_todo_service, _cancel_event=None):
        raise AssertionError("sub-agents should not run when filter matches nothing")

    monkeypatch.setattr(agent, "_run_sub_agents", fail_if_sub_agents_called)

    todo_service = FakeTodoService()
    result = await agent.run(todo_service, session_id="task_1")

    assert result["success"] is False
    assert "所选检查项大类在规则库中均不存在" in result["error"]
    assert todo_service.created == []
