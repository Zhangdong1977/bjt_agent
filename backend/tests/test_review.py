"""Review module tests.

Test cases:
- REV-001: Start review
- REV-002: Duplicate start interception
- REV-003: Missing documents warning
- REV-004: Get review results
- REV-005: Cancel task
- REV-006: Get task status
- REV-007: Get task steps
- REV-008: Get findings
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


class TestReviewStart:
    """Tests for starting a review."""

    @pytest.mark.asyncio
    async def test_start_review_success(self, client: AsyncClient, auth_headers: dict):
        """REV-001: Start review when documents are ready."""
        project = await create_test_project(client, auth_headers, "Review Test Project")

        response = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["project_id"] == project["id"]
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_start_review_duplicate(
        self, client: AsyncClient, auth_headers: dict
    ):
        """REV-002: Prevent starting duplicate review."""
        project = await create_test_project(client, auth_headers, "Duplicate Review Test")

        # Start first review
        response1 = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        assert response1.status_code == 201

        # Try to start second review
        response2 = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        assert response2.status_code == 400
        assert "already running" in response2.json()["detail"]


class TestReviewGet:
    """Tests for getting review results."""

    @pytest.mark.asyncio
    async def test_get_review_results_empty(self, client: AsyncClient, auth_headers: dict):
        """REV-004: Get review results when no completed review exists."""
        project = await create_test_project(client, auth_headers, "Empty Results Test")

        response = await client.get(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "summary" in data
        assert "findings" in data
        assert data["summary"]["total_requirements"] == 0


class TestReviewTaskStatus:
    """Tests for getting review task status."""

    @pytest.mark.asyncio
    async def test_get_task_status(self, client: AsyncClient, auth_headers: dict):
        """REV-006: Get task status."""
        project = await create_test_project(client, auth_headers, "Task Status Test")

        # Start a review
        start_response = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        task_id = start_response.json()["id"]

        # Get task status
        response = await client.get(
            f"/api/projects/{project['id']}/review/tasks/{task_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == task_id
        assert data["project_id"] == project["id"]

    @pytest.mark.asyncio
    async def test_get_task_status_not_found(
        self, client: AsyncClient, auth_headers: dict
    ):
        """REV-006: Get status of non-existent task."""
        project = await create_test_project(client, auth_headers, "NonExistent Task Test")
        fake_task_id = str(uuid.uuid4())

        response = await client.get(
            f"/api/projects/{project['id']}/review/tasks/{fake_task_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestReviewCancel:
    """Tests for cancelling a review task."""

    @pytest.mark.asyncio
    async def test_cancel_task(self, client: AsyncClient, auth_headers: dict):
        """REV-005: Cancel a running task."""
        project = await create_test_project(client, auth_headers, "Cancel Test")

        # Start a review
        start_response = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        task_id = start_response.json()["id"]

        # Cancel the task
        response = await client.post(
            f"/api/projects/{project['id']}/review/tasks/{task_id}/cancel",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_task_not_found(self, client: AsyncClient, auth_headers: dict):
        """REV-005: Cancel non-existent task."""
        project = await create_test_project(client, auth_headers, "Cancel NonExistent")
        fake_task_id = str(uuid.uuid4())

        response = await client.post(
            f"/api/projects/{project['id']}/review/tasks/{fake_task_id}/cancel",
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestReviewTaskSteps:
    """Tests for getting review task steps."""

    @pytest.mark.asyncio
    async def test_get_task_steps(self, client: AsyncClient, auth_headers: dict):
        """REV-007: Get task steps."""
        project = await create_test_project(client, auth_headers, "Steps Test")

        # Start a review
        start_response = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        task_id = start_response.json()["id"]

        # Get task steps
        response = await client.get(
            f"/api/projects/{project['id']}/review/tasks/{task_id}/steps",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Steps is a list (may be empty if not yet executed)
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_steps_not_found(self, client: AsyncClient, auth_headers: dict):
        """REV-007: Get steps of non-existent task."""
        project = await create_test_project(client, auth_headers, "Steps NonExistent")
        fake_task_id = str(uuid.uuid4())

        response = await client.get(
            f"/api/projects/{project['id']}/review/tasks/{fake_task_id}/steps",
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestReviewTaskResults:
    """Tests for getting review task results/findings."""

    @pytest.mark.asyncio
    async def test_get_task_results(self, client: AsyncClient, auth_headers: dict):
        """REV-008: Get task findings."""
        project = await create_test_project(client, auth_headers, "Results Test")

        # Start a review
        start_response = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        task_id = start_response.json()["id"]

        # Get task results
        response = await client.get(
            f"/api/projects/{project['id']}/review/tasks/{task_id}/results",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Results is a list (may be empty if not yet completed)
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_results_not_found(self, client: AsyncClient, auth_headers: dict):
        """REV-008: Get results of non-existent task."""
        project = await create_test_project(client, auth_headers, "Results NonExistent")
        fake_task_id = str(uuid.uuid4())

        response = await client.get(
            f"/api/projects/{project['id']}/review/tasks/{fake_task_id}/results",
            headers=auth_headers,
        )

        assert response.status_code == 404
