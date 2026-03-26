"""SSE (Server-Sent Events) streaming tests.

Test cases:
- SSE-001: Stream events for a task
- SSE-002: Stream with invalid task ID returns 404
- SSE-003: Stream without authentication returns 401
- SSE-004: Stream for another user's task returns 404
"""

import uuid
import pytest
from httpx import AsyncClient


async def create_test_project(
    client: AsyncClient,
    auth_headers: dict,
    name: str = "Test Project",
    description: str = "Test Description",
) -> dict:
    """Helper to create a test project."""
    response = await client.post(
        "/api/projects",
        json={"name": name, "description": description},
        headers=auth_headers,
    )
    return response.json()


class TestSSEStream:
    """Tests for SSE event streaming endpoint."""

    @pytest.mark.asyncio
    async def test_stream_without_auth(self, client: AsyncClient):
        """SSE-003: Stream without authentication returns 401."""
        project_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())

        response = await client.get(
            f"/api/projects/{project_id}/review/tasks/{task_id}/stream",
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_stream_invalid_token(self, client: AsyncClient):
        """SSE-003: Stream with invalid token returns 401."""
        project_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())

        response = await client.get(
            f"/api/projects/{project_id}/review/tasks/{task_id}/stream",
            headers={"Authorization": "Bearer invalid_token"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_stream_nonexistent_project(self, client: AsyncClient, auth_headers: dict):
        """SSE-001: Stream for non-existent project returns 404."""
        fake_project_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())

        response = await client.get(
            f"/api/projects/{fake_project_id}/review/tasks/{task_id}/stream",
            headers=auth_headers,
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_stream_nonexistent_task(self, client: AsyncClient, auth_headers: dict):
        """SSE-002: Stream with invalid task ID returns 404."""
        project = await create_test_project(client, auth_headers, "SSE Task Test")
        fake_task_id = str(uuid.uuid4())

        response = await client.get(
            f"/api/projects/{project['id']}/review/tasks/{fake_task_id}/stream",
            headers=auth_headers,
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_stream_cross_user_access_denied(
        self, client: AsyncClient, auth_headers: dict
    ):
        """SSE-004: User cannot stream another user's task."""
        # User A creates project and starts review
        project = await create_test_project(client, auth_headers, "User A SSE Project")

        start_response = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        task_id = start_response.json()["id"]

        # User B tries to stream User A's task
        username = f"user_b_{uuid.uuid4().hex[:8]}"
        email = f"{username}@example.com"
        password = "Test123!"

        await client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )

        login_response = await client.post(
            "/api/auth/login",
            data={"username": username, "password": password},
        )
        user_b_token = login_response.json()["access_token"]
        user_b_headers = {"Authorization": f"Bearer {user_b_token}"}

        response = await client.get(
            f"/api/projects/{project['id']}/review/tasks/{task_id}/stream",
            headers=user_b_headers,
        )

        # Should return 404 (not 403, to avoid leaking existence)
        assert response.status_code == 404
