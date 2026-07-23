"""Isolated duplicate-check tests; no configured database or network is used."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.agent.duplicate_check_agent import DuplicateCheckAgent
from backend.agent.duplicate_master_agent import summarize_sub_agent_results
from backend.api import duplicate_check as duplicate_api
from backend.api import documents as documents_api
from backend.api import feedback as feedback_api
from backend.api import review as review_api
from backend.api import share as share_api
from backend.api.deps import create_access_token
from backend import main as main_api
from backend.schemas.document import DuplicatePairAttachRequest


class _ScalarRows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, *, one=None, rows=(), scalar_rows=None):
        self._one = one
        self._rows = list(rows)
        self._scalar_rows = list(scalar_rows if scalar_rows is not None else rows)

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return _ScalarRows(self._scalar_rows)

    def all(self):
        return list(self._rows)


class _SequenceDB:
    def __init__(self, *results):
        self.results = list(results)

    async def execute(self, _query):
        assert self.results, "unexpected database execute"
        return self.results.pop(0)

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def refresh(self, _value):
        return None


class _SessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_external_user_cannot_read_duplicate_agent_steps():
    with pytest.raises(HTTPException) as exc:
        await duplicate_api.get_duplicate_steps(
            project_id="project-1",
            task_id="task-1",
            db=_SequenceDB(),
            current_user=SimpleNamespace(id="user-1"),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_shared_task_sse_filters_external_tool_timeline(monkeypatch):
    task = SimpleNamespace(id="task-1", project_id="project-1")
    project = SimpleNamespace(id="project-1", user_id="user-1", is_deleted=False)
    db = _SequenceDB(_Result(one=task), _Result(one=project))
    monkeypatch.setattr(
        "backend.models.async_session_factory", lambda: _SessionContext(db)
    )

    async def event_stream(_task_id):
        yield f"data: {json.dumps({'type': 'sub_agent_tool_call_end'})}\n\n"
        yield f"data: {json.dumps({'type': 'status', 'status': 'running'})}\n\n"

    monkeypatch.setattr(main_api.sse_manager, "connect", event_stream)
    token = create_access_token({"sub": "user-1", "interior_user": False})

    response = await main_api.stream_task_events(task.id, token)
    events = [event async for event in response.body_iterator]

    assert len(events) == 1
    assert '"type": "status"' in events[0]
    assert "tool_call" not in events[0]


@pytest.mark.asyncio
async def test_shared_task_sse_hides_deleted_project_from_external_user(monkeypatch):
    task = SimpleNamespace(id="task-1", project_id="project-1")
    project = SimpleNamespace(id="project-1", user_id="user-1", is_deleted=True)
    db = _SequenceDB(_Result(one=task), _Result(one=project))
    monkeypatch.setattr(
        "backend.models.async_session_factory", lambda: _SessionContext(db)
    )
    token = create_access_token({"sub": "user-1", "interior_user": False})

    with pytest.raises(HTTPException) as exc:
        await main_api.stream_task_events(task.id, token)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_project_type_isolation_in_both_api_namespaces():
    user = SimpleNamespace(id="user-1")
    review_project = SimpleNamespace(
        id="project-1", user_id=user.id, project_type="review", is_deleted=False
    )
    duplicate_project = SimpleNamespace(
        id="project-2", user_id=user.id, project_type="duplicate", is_deleted=False
    )

    with pytest.raises(HTTPException) as duplicate_exc:
        await duplicate_api._project(
            review_project.id,
            user,
            _SequenceDB(_Result(one=review_project)),
        )
    assert duplicate_exc.value.status_code == 404

    with pytest.raises(HTTPException) as review_exc:
        await review_api.verify_project_ownership(
            duplicate_project.id,
            user,
            _SequenceDB(_Result(one=duplicate_project)),
        )
    assert review_exc.value.status_code == 400

    with pytest.raises(HTTPException) as cross_user_exc:
        await review_api.verify_project_ownership(
            duplicate_project.id,
            SimpleNamespace(id="other-user"),
            _SequenceDB(_Result(one=duplicate_project)),
        )
    assert cross_user_exc.value.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_project_is_not_connected_to_mvp_share_or_feedback():
    user = SimpleNamespace(id="user-1")
    project = SimpleNamespace(
        id="project-1", user_id=user.id, project_type="duplicate", is_deleted=False
    )

    with pytest.raises(HTTPException) as share_exc:
        await share_api._verify_my_project(
            project.id, user, _SequenceDB(_Result(one=project))
        )
    assert share_exc.value.status_code == 400

    with pytest.raises(HTTPException) as feedback_exc:
        await feedback_api._verify_project(
            project.id, user, _SequenceDB(_Result(one=project))
        )
    assert feedback_exc.value.status_code == 400


@pytest.mark.asyncio
async def test_duplicate_draft_rejects_second_file_on_same_side():
    existing = SimpleNamespace(id="document-1")
    with pytest.raises(HTTPException) as exc:
        await documents_api.upload_draft_document(
            db=_SequenceDB(_Result(scalar_rows=[existing])),
            doc_type="duplicate_left",
            file=None,
            current_user=SimpleNamespace(id="user-1"),
        )
    assert exc.value.status_code == 400
    assert "A方仅允许上传一份文件" in exc.value.detail


@pytest.mark.asyncio
async def test_duplicate_pair_is_attached_atomically_after_role_validation():
    user = SimpleNamespace(id="user-1")
    project = SimpleNamespace(
        id="project-1", user_id=user.id, project_type="duplicate", is_deleted=False
    )
    left = SimpleNamespace(
        id="left-1",
        owner_user_id=user.id,
        project_id=None,
        doc_type="duplicate_left",
        status="parsed",
    )
    right = SimpleNamespace(
        id="right-1",
        owner_user_id=user.id,
        project_id=None,
        doc_type="duplicate_right",
        status="parsed",
    )
    db = _SequenceDB(
        _Result(one=project),
        _Result(scalar_rows=[left, right]),
        _Result(scalar_rows=[]),
    )

    attached = await documents_api.attach_duplicate_pair(
        DuplicatePairAttachRequest(
            left_document_id=left.id,
            right_document_id=right.id,
        ),
        project.id,
        db,
        user,
    )

    assert attached == [left, right]
    assert left.project_id == project.id
    assert right.project_id == project.id


@pytest.mark.asyncio
async def test_duplicate_results_are_grouped_and_do_not_expose_rule_paths():
    now = datetime.now(timezone.utc)
    user = SimpleNamespace(id="user-1")
    project = SimpleNamespace(
        id="project-1", user_id=user.id, project_type="duplicate", is_deleted=False
    )
    task = SimpleNamespace(id="task-1", project_id=project.id, task_type="duplicate")
    todo = SimpleNamespace(
        id="todo-1",
        project_id=project.id,
        session_id=task.id,
        rule_doc_path="/srv/private/rules/D001.md",
        rule_doc_name="D001.md",
        check_items=[{"id": "item-1", "title": "人员承诺措辞"}],
        status="completed",
        result={"finding_count": 1},
        error_message=None,
        retry_count=0,
        max_retries=1,
        max_steps=2,
        brain_capacity=1.0,
        started_at=now,
        completed_at=now,
        created_at=now,
    )
    finding = SimpleNamespace(
        id="finding-1",
        task_id=task.id,
        todo_id=todo.id,
        rule_doc_name=todo.rule_doc_name,
        check_item_name="人员承诺措辞",
        verdict="suspicious",
        similarity_score=Decimal("0.9876"),
        match_type="near_exact",
        left_document_id="left-1",
        left_excerpt="余必亲临，昼夜督造",
        left_location={"section": "项目经理承诺", "start_line": 12, "end_line": 12},
        right_document_id="right-1",
        right_excerpt="余必亲临，昼夜督造",
        right_location={"section": "驻场承诺", "start_line": 18, "end_line": 18},
        explanation="双方采用相同的非标准文言式承诺。",
        suggestion="人工复核原始文件。",
        evidence={"candidate_id": "candidate-1"},
        created_at=now,
    )
    db = _SequenceDB(
        _Result(one=project),
        _Result(one=task),
        _Result(scalar_rows=[todo]),
        _Result(scalar_rows=[finding]),
        _Result(rows=[("left-1", "A.docx"), ("right-1", "B.docx")]),
    )

    response = await duplicate_api.get_duplicate_results(
        project.id, task.id, db, user
    )

    assert response.summary.rule_count == 1
    assert response.summary.completed_rule_count == 1
    assert response.summary.suspicious_count == 1
    assert response.findings[0].similarity_score == pytest.approx(0.9876)
    assert response.findings[0].left_filename == "A.docx"
    assert "rule_doc_path" not in response.todos[0].model_dump()


def test_partial_and_all_failed_sub_agent_summaries():
    partial = summarize_sub_agent_results(
        [
            {"success": True, "finding_count": 3},
            {"success": False, "error": "bad rule"},
            RuntimeError("provider unavailable"),
        ]
    )
    assert partial == {"total": 3, "completed": 1, "failed": 2, "finding_count": 3}

    all_failed = summarize_sub_agent_results(
        [{"success": False}, RuntimeError("failed")]
    )
    assert all_failed["completed"] == 0
    assert all_failed["failed"] == 2


@pytest.mark.asyncio
async def test_in_flight_llm_wait_stops_on_cancellation():
    class NeverReturningClient:
        async def generate(self, *, messages):
            await asyncio.Event().wait()

    agent = object.__new__(DuplicateCheckAgent)
    agent.cancel_event = asyncio.Event()
    running = asyncio.create_task(
        agent._generate_with_cancellation(NeverReturningClient(), ["message"])
    )
    await asyncio.sleep(0)
    agent.cancel_event.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, timeout=1)


def test_migration_has_concurrent_single_side_guards():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "022_create_duplicate_check_v2.sql"
    ).read_text(encoding="utf-8")
    assert "ux_documents_duplicate_draft_side" in migration
    assert "ux_documents_duplicate_project_side" in migration
