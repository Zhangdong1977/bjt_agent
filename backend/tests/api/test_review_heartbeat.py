"""Tests for review heartbeat API endpoint."""

import sys
from pathlib import Path

# Ensure project root is in sys.path for backend module imports
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

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


class TestHeartbeatEndpoint:
    """Tests for the heartbeat endpoint."""

    @pytest.mark.asyncio
    async def test_heartbeat_updates_timestamp(self, client: AsyncClient, auth_headers: dict):
        """Test that heartbeat endpoint updates last_heartbeat."""
        # Create project
        project = await create_test_project(client, auth_headers, "Heartbeat Test Project")

        # Start a review to create a running task
        start_response = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        assert start_response.status_code == 201
        task = start_response.json()

        # Wait a moment for task to be picked up and start running
        import asyncio
        await asyncio.sleep(1)

        # Call heartbeat endpoint
        response = await client.post(
            f"/api/projects/{project['id']}/review/tasks/{task['id']}/heartbeat",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "last_heartbeat" in data

    @pytest.mark.asyncio
    async def test_heartbeat_returns_status_for_completed_task(self, client: AsyncClient, auth_headers: dict):
        """Test heartbeat returns status for completed tasks."""
        # Create project
        project = await create_test_project(client, auth_headers, "Completed Task Heartbeat Test")

        # Start a review
        start_response = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        assert start_response.status_code == 201
        task = start_response.json()

        # Get task status to check current status
        status_response = await client.get(
            f"/api/projects/{project['id']}/review/tasks/{task['id']}",
            headers=auth_headers,
        )
        task_data = status_response.json()

        # If task is still pending or running, try to cancel it
        if task_data["status"] in ["pending", "running"]:
            cancel_response = await client.post(
                f"/api/projects/{project['id']}/review/tasks/{task['id']}/cancel",
                headers=auth_headers,
            )
            assert cancel_response.status_code == 200

        # Now call heartbeat on the cancelled/completed task
        response = await client.post(
            f"/api/projects/{project['id']}/review/tasks/{task['id']}/heartbeat",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Should return status but indicate task is not running
        assert data["status"] in ["cancelled", "completed", "failed"]

    @pytest.mark.asyncio
    async def test_heartbeat_returns_404_for_nonexistent_task(self, client: AsyncClient, auth_headers: dict):
        """Test heartbeat returns 404 for non-existent task."""
        # Create project
        project = await create_test_project(client, auth_headers, "Non-existent Task Test")

        fake_task_id = "nonexistent-task-id-12345"

        response = await client.post(
            f"/api/projects/{project['id']}/review/tasks/{fake_task_id}/heartbeat",
            headers=auth_headers,
        )

        assert response.status_code == 404