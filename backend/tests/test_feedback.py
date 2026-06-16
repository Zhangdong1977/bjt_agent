"""Feedback API cross-user access tests.

Covers the fix for: interior users viewing another user's project feedback
from the experience dashboard must not receive a 404 "Project not found".
Mirrors test_documents.TestDocumentInteriorAccess.

Test cases:
- FB-INT-001: interior user reads project feedback history (all users) -> 200
- FB-INT-002: regular user reading another's project feedback history -> 404
- FB-INT-003: interior user reads per-finding feedback -> 200
- FB-INT-004: regular user's history still scoped to own feedback (no regression)
"""

import uuid

import pytest
from httpx import AsyncClient

from backend.experience.models import ExperienceFeedback
from backend.models import async_session_factory
from backend.models.review_result import ReviewResult
from backend.models.review_task import ReviewTask
from backend.tests.test_documents import create_test_project


async def _seed_feedback(
    project_id: str,
    user_id: str,
    *,
    feedback_type: str = "confirm",
    comment: str | None = None,
    status: str = "accepted",
) -> str:
    """Insert a ReviewTask + non-compliant ReviewResult + ExperienceFeedback row.

    Returns the finding (ReviewResult) id. Sets up cross-user feedback data
    without going through the submission API.
    """
    async with async_session_factory() as session:
        task = ReviewTask(project_id=project_id, status="completed")
        session.add(task)
        await session.flush()

        finding = ReviewResult(
            task_id=task.id,
            requirement_key=f"req-{uuid.uuid4().hex[:8]}",
            requirement_content="测试要求内容",
            severity="major",
            is_compliant=False,
        )
        session.add(finding)
        await session.flush()

        feedback = ExperienceFeedback(
            finding_id=finding.id,
            user_id=user_id,
            project_id=project_id,
            task_id=task.id,
            feedback_type=feedback_type,
            status=status,
            confidence_delta=0.05,
            comment=comment,
        )
        session.add(feedback)
        await session.commit()
        return finding.id


async def _current_user_id(client: AsyncClient, headers: dict) -> str:
    """Resolve the user id encoded in the given auth token via /auth/me."""
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    return resp.json()["id"]


class TestFeedbackInteriorAccess:
    """Cross-user feedback access for interior (admin) users.

    Interior users reviewing another user's project from the experience
    dashboard must be able to *read* feedback (history / per-finding). Regular
    users remain fully isolated, and their own history is still scoped to their
    own feedback.
    """

    @pytest.mark.asyncio
    async def test_interior_user_gets_project_feedback_history(
        self, client: AsyncClient, auth_headers: dict, interior_auth_headers: dict
    ):
        """Interior user sees ALL feedback on another user's project (no user_id filter)."""
        project = await create_test_project(client, auth_headers, "Owner Project")
        owner_id = await _current_user_id(client, auth_headers)
        await _seed_feedback(project["id"], owner_id, comment="owner-feedback")

        resp = await client.get(
            f"/api/projects/{project['id']}/feedback/history",
            headers=interior_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert any(fb["comment"] == "owner-feedback" for fb in data)

    @pytest.mark.asyncio
    async def test_regular_user_cannot_get_others_feedback_history(
        self, client: AsyncClient, auth_headers: dict, interior_auth_headers: dict
    ):
        """Regular user is isolated: another user's project feedback history -> 404."""
        # interior user owns the project
        project = await create_test_project(
            client, interior_auth_headers, "Interior Owner Project"
        )

        resp = await client.get(
            f"/api/projects/{project['id']}/feedback/history",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_interior_user_gets_finding_feedback(
        self, client: AsyncClient, auth_headers: dict, interior_auth_headers: dict
    ):
        """Interior user can read per-finding feedback on another user's project."""
        project = await create_test_project(client, auth_headers, "Owner Project")
        owner_id = await _current_user_id(client, auth_headers)
        finding_id = await _seed_feedback(project["id"], owner_id)

        resp = await client.get(
            f"/api/projects/{project['id']}/findings/{finding_id}/feedback",
            headers=interior_auth_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    @pytest.mark.asyncio
    async def test_regular_user_history_returns_own_only(
        self, client: AsyncClient, auth_headers: dict, interior_auth_headers: dict
    ):
        """Regular user's history is still filtered to their own feedback (no regression)."""
        project = await create_test_project(client, auth_headers, "Owner Project")
        owner_id = await _current_user_id(client, auth_headers)
        other_id = await _current_user_id(client, interior_auth_headers)

        await _seed_feedback(project["id"], owner_id, comment="mine")
        await _seed_feedback(project["id"], other_id, comment="theirs")

        resp = await client.get(
            f"/api/projects/{project['id']}/feedback/history",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        comments = {fb["comment"] for fb in resp.json()}
        assert "mine" in comments
        assert "theirs" not in comments
