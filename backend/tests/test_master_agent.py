"""Tests for MasterAgent task-level failure handling."""

from types import SimpleNamespace

import pytest

from backend.agent.master.master_agent import MasterAgent


class FakeTodoService:
    async def create_todo(self, **kwargs):
        return SimpleNamespace(
            id=kwargs["rule_doc_name"],
            project_id=kwargs["project_id"],
            rule_doc_path=kwargs["rule_doc_path"],
            rule_doc_name=kwargs["rule_doc_name"],
        )


@pytest.mark.asyncio
async def test_master_agent_all_sub_agents_failed_returns_failure(monkeypatch):
    """All failed sub-agents must not be reported as a completed review."""
    events = []
    agent = MasterAgent(
        project_id="project_1",
        rule_library_path="/rules",
        tender_docs=[("tender.md", "/tmp/tender.md")],
        bid_docs=[("bid.md", "/tmp/bid.md")],
        user_id="user_1",
        event_callback=lambda event_type, data: events.append((event_type, data)),
    )

    async def fake_scan(_path):
        return SimpleNamespace(
            success=True,
            content='{"rule_docs":[{"name":"r1.md","path":"/rules/r1.md"},{"name":"r2.md","path":"/rules/r2.md"}]}',
        )

    async def fake_run_sub_agents(_todo_service, _cancel_event=None):
        return {
            "total": 2,
            "completed": 0,
            "failed": 2,
            "cancelled": 0,
            "max_retries_exceeded": 1,
            "exceptions": 0,
        }

    async def fail_if_aggregate_called(_todo_service):
        raise AssertionError("aggregate should not run when all sub-agents failed")

    monkeypatch.setattr(agent.scanner, "execute", fake_scan)
    monkeypatch.setattr(agent, "_run_sub_agents", fake_run_sub_agents)
    monkeypatch.setattr(agent, "_simple_aggregate", fail_if_aggregate_called)

    result = await agent.run(FakeTodoService(), session_id="task_1")

    assert result["success"] is False
    assert "所有子代理审查均失败" in result["error"]
    assert result["sub_agent_stats"]["failed"] == 2
    assert any(event_type == "error" for event_type, _ in events)
