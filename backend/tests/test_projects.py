"""Project management module tests.

Test cases:
- PROJ-001: Create project
- PROJ-002: List projects
- PROJ-003: Get project details
- PROJ-004: Update project
- PROJ-005: Delete project
- PROJ-006: Cross-user access denial
"""

import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.models import Project, async_session_factory, engine


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


class TestProjectCreate:
    """Tests for project creation endpoint."""

    @pytest.mark.asyncio
    async def test_create_project_success(self, client: AsyncClient, auth_headers: dict):
        """PROJ-001: Create a new project successfully."""
        response = await client.post(
            "/api/projects",
            json={"name": "Test Project", "description": "Test Description"},
            headers=auth_headers,
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["name"] == "Test Project"
        assert data["description"] == "Test Description"
        assert data["status"] == "draft"

    @pytest.mark.asyncio
    async def test_create_project_without_description(
        self, client: AsyncClient, auth_headers: dict
    ):
        """PROJ-001: Create project without description."""
        response = await client.post(
            "/api/projects",
            json={"name": "Test Project Only Name"},
            headers=auth_headers,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Project Only Name"


class TestProjectList:
    """Tests for project listing endpoint."""

    @pytest.mark.asyncio
    async def test_list_projects(self, client: AsyncClient, auth_headers: dict):
        """PROJ-002: List all projects for current user."""
        # Create multiple projects
        await create_test_project(client, auth_headers, "Project 1")
        await create_test_project(client, auth_headers, "Project 2")
        await create_test_project(client, auth_headers, "Project 3")

        response = await client.get("/api/projects", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "projects" in data
        assert len(data["projects"]) >= 3

    @pytest.mark.asyncio
    async def test_list_projects_empty(self, client: AsyncClient, auth_headers: dict):
        """PROJ-002: List projects when user has none."""
        response = await client.get("/api/projects", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "projects" in data


class TestProjectGet:
    """Tests for getting project details."""

    @pytest.mark.asyncio
    async def test_get_project_success(self, client: AsyncClient, auth_headers: dict):
        """PROJ-003: Get project details."""
        project = await create_test_project(client, auth_headers, "Detail Test Project")

        response = await client.get(
            f"/api/projects/{project['id']}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == project["id"]
        assert data["name"] == "Detail Test Project"

    @pytest.mark.asyncio
    async def test_get_project_not_found(self, client: AsyncClient, auth_headers: dict):
        """PROJ-003: Get non-existent project."""
        fake_id = str(uuid.uuid4())
        response = await client.get(
            f"/api/projects/{fake_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestProjectUpdate:
    """Tests for project update endpoint."""

    @pytest.mark.asyncio
    async def test_update_project_success(self, client: AsyncClient, auth_headers: dict):
        """PROJ-004: Update project successfully."""
        project = await create_test_project(client, auth_headers, "Original Name")

        response = await client.put(
            f"/api/projects/{project['id']}",
            json={"name": "Updated Name", "description": "Updated Description"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"
        assert data["description"] == "Updated Description"

    @pytest.mark.asyncio
    async def test_update_project_partial(self, client: AsyncClient, auth_headers: dict):
        """PROJ-004: Partial update - name only."""
        project = await create_test_project(
            client, auth_headers, "Original", "Original Description"
        )

        response = await client.put(
            f"/api/projects/{project['id']}",
            json={"name": "New Name Only"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Name Only"
        assert data["description"] == "Original Description"


class TestProjectDelete:
    """Tests for project deletion endpoint."""

    @pytest.mark.asyncio
    async def test_delete_project_success(self, client: AsyncClient, auth_headers: dict):
        """PROJ-005: Soft-delete project successfully."""
        project = await create_test_project(client, auth_headers, "To Delete")

        response = await client.delete(
            f"/api/projects/{project['id']}",
            headers=auth_headers,
        )

        assert response.status_code == 204

        # Owner can no longer see deleted project.
        get_response = await client.get(
            f"/api/projects/{project['id']}",
            headers=auth_headers,
        )
        assert get_response.status_code == 404

        list_response = await client.get("/api/projects", headers=auth_headers)
        assert list_response.status_code == 200
        ids = {p["id"] for p in list_response.json()["projects"]}
        assert project["id"] not in ids

        # The row is retained so internal users can still inspect related data.
        async with async_session_factory() as session:
            result = await session.execute(
                select(Project).where(Project.id == project["id"])
            )
            db_project = result.scalar_one()
            assert db_project.is_deleted is True
            assert db_project.deleted_at is not None
            assert db_project.deleted_by_user_id is not None
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_deleted_project_remains_visible_to_interior_user(
        self,
        client: AsyncClient,
        auth_headers: dict,
        interior_auth_headers: dict,
    ):
        """PROJ-005: Internal users can still read soft-deleted projects."""
        project = await create_test_project(client, auth_headers, "Interior Visible")

        response = await client.delete(
            f"/api/projects/{project['id']}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        owner_response = await client.get(
            f"/api/projects/{project['id']}",
            headers=auth_headers,
        )
        assert owner_response.status_code == 404

        interior_response = await client.get(
            f"/api/projects/{project['id']}",
            headers=interior_auth_headers,
        )
        assert interior_response.status_code == 200
        data = interior_response.json()
        assert data["id"] == project["id"]
        assert data["is_deleted"] is True

    @pytest.mark.asyncio
    async def test_interior_user_can_soft_delete_any_project(
        self,
        client: AsyncClient,
        auth_headers: dict,
        interior_auth_headers: dict,
    ):
        """PROJ-005: Interior users may soft-delete projects they do not own."""
        project = await create_test_project(client, auth_headers, "Interior Delete")

        response = await client.delete(
            f"/api/projects/{project['id']}",
            headers=interior_auth_headers,
        )
        assert response.status_code == 204

        owner_response = await client.get(
            f"/api/projects/{project['id']}",
            headers=auth_headers,
        )
        assert owner_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_project_not_found(self, client: AsyncClient, auth_headers: dict):
        """PROJ-005: Delete non-existent project."""
        fake_id = str(uuid.uuid4())
        response = await client.delete(
            f"/api/projects/{fake_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestProjectAccessControl:
    """Tests for project access control."""

    @pytest.mark.asyncio
    async def test_cross_user_access_denied(
        self, client: AsyncClient, auth_headers: dict
    ):
        """PROJ-006: User cannot access another user's project."""
        # Create project with user A
        project = await create_test_project(client, auth_headers, "User A Project")

        # Create new user B
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

        # User B tries to access User A's project
        response = await client.get(
            f"/api/projects/{project['id']}",
            headers=user_b_headers,
        )

        assert response.status_code == 404  # Should return 404 (not 403, to not leak existence)

    @pytest.mark.asyncio
    async def test_user_cannot_delete_others_project(
        self, client: AsyncClient, auth_headers: dict
    ):
        """PROJ-006: User cannot delete another user's project."""
        # Create project with user A
        project = await create_test_project(client, auth_headers, "User A Project")

        # Create user B
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

        # User B tries to delete User A's project
        response = await client.delete(
            f"/api/projects/{project['id']}",
            headers=user_b_headers,
        )

        assert response.status_code == 404
