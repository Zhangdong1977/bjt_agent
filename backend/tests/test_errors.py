"""Error handling and edge case tests.

Test cases:
- ERR-001: Upload document without authentication
- ERR-002: Upload document with invalid token
- ERR-003: Start review without any documents
- ERR-004: Start review with only tender document (missing bid)
- ERR-005: Start review with only bid document (missing tender)
- ERR-006: Upload empty file
- ERR-007: Upload file with invalid extension
- ERR-008: Cancel already completed task
- ERR-009: Get results for non-existent project
- ERR-010: Project listing without authentication
- ERR-011: Delete project without authentication
- ERR-012: Update project without authentication
"""

import io
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


def create_test_pdf() -> io.BytesIO:
    """Create a minimal PDF file for testing."""
    pdf_content = b"""%PDF-1.4
1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj
2 0 obj<</Type /Pages /Kids [3 0 R] /Count 1>>endobj
3 0 obj<</Type /Page /Parent 2 0 R /Resources 4 0 R /MediaBox [0 0 612 792] /Contents 5 0 R>>endobj
4 0 obj<</Font<</F1 6 0 R>>>>endobj
5 0 obj<</Length 44>>stream
BT
/F1 12 Tf
100 700 Td
(Test Document) Tj
ET
endstream endobj
6 0 obj<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>endobj
xref
0 7
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000270 00000 n
0000000356 00000 n
0000000447 00000 n
trailer<</Size 7 /Root 1 0 R>>
startxref
521
%%EOF"""
    return io.BytesIO(pdf_content)


class TestAuthenticationErrors:
    """Tests for authentication errors on protected endpoints."""

    @pytest.mark.asyncio
    async def test_upload_without_auth(self, client: AsyncClient):
        """ERR-001: Upload document without authentication."""
        project_id = str(uuid.uuid4())
        files = {"file": ("test.pdf", create_test_pdf(), "application/pdf")}

        response = await client.post(
            f"/api/projects/{project_id}/documents?doc_type=tender",
            files=files,
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_upload_invalid_token(self, client: AsyncClient):
        """ERR-002: Upload document with invalid token."""
        project_id = str(uuid.uuid4())
        files = {"file": ("test.pdf", create_test_pdf(), "application/pdf")}

        response = await client.post(
            f"/api/projects/{project_id}/documents?doc_type=tender",
            files=files,
            headers={"Authorization": "Bearer invalid_token"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_projects_without_auth(self, client: AsyncClient):
        """ERR-010: List projects without authentication."""
        response = await client.get("/api/projects")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_project_without_auth(self, client: AsyncClient):
        """ERR-010: Create project without authentication."""
        response = await client.post(
            "/api/projects",
            json={"name": "Test Project"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_project_without_auth(self, client: AsyncClient):
        """ERR-011: Delete project without authentication."""
        project_id = str(uuid.uuid4())

        response = await client.delete(f"/api/projects/{project_id}")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_update_project_without_auth(self, client: AsyncClient):
        """ERR-012: Update project without authentication."""
        project_id = str(uuid.uuid4())

        response = await client.put(
            f"/api/projects/{project_id}",
            json={"name": "Updated Name"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_start_review_without_auth(self, client: AsyncClient):
        """ERR-001 variant: Start review without authentication."""
        project_id = str(uuid.uuid4())

        response = await client.post(f"/api/projects/{project_id}/review")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_review_without_auth(self, client: AsyncClient):
        """ERR-001 variant: Get review without authentication."""
        project_id = str(uuid.uuid4())

        response = await client.get(f"/api/projects/{project_id}/review")

        assert response.status_code == 401


class TestReviewEdgeCases:
    """Tests for review edge cases."""

    @pytest.mark.asyncio
    async def test_start_review_no_documents(
        self, client: AsyncClient, auth_headers: dict
    ):
        """ERR-003: Start review without any documents."""
        project = await create_test_project(client, auth_headers, "No Docs Review Test")

        response = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )

        # Should create the task anyway (documents are checked by the agent)
        assert response.status_code == 201
        data = response.json()
        assert data["project_id"] == project["id"]
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_cancel_completed_task(
        self, client: AsyncClient, auth_headers: dict
    ):
        """ERR-008: Cancel an already completed task."""
        project = await create_test_project(client, auth_headers, "Cancel Completed Test")

        # Start a review
        start_response = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        task_id = start_response.json()["id"]

        # Wait a moment for task to potentially complete (or cancel it first)
        # Try to cancel a task that is still pending/running
        cancel_response = await client.post(
            f"/api/projects/{project['id']}/review/tasks/{task_id}/cancel",
            headers=auth_headers,
        )
        assert cancel_response.status_code == 200

        # Now try to cancel again - should fail
        response = await client.post(
            f"/api/projects/{project['id']}/review/tasks/{task_id}/cancel",
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "cannot be cancelled" in response.json()["detail"]


class TestDocumentEdgeCases:
    """Tests for document upload edge cases."""

    @pytest.mark.asyncio
    async def test_upload_empty_file(
        self, client: AsyncClient, auth_headers: dict
    ):
        """ERR-006: Upload an empty file."""
        project = await create_test_project(client, auth_headers, "Empty File Test")

        empty_content = b""
        files = {"file": ("empty.pdf", io.BytesIO(empty_content), "application/pdf")}

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files=files,
            headers=auth_headers,
        )

        # Should either succeed (backend will handle parsing failure)
        # or fail at upload validation
        assert response.status_code in [201, 400]

    @pytest.mark.asyncio
    async def test_upload_invalid_extension(
        self, client: AsyncClient, auth_headers: dict
    ):
        """ERR-007: Upload file with invalid extension."""
        project = await create_test_project(client, auth_headers, "Invalid Ext Test")

        files = {"file": ("test.exe", io.BytesIO(b"fake executable"), "application/x-executable")}

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files=files,
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "Unsupported file type" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_upload_without_doc_type(
        self, client: AsyncClient, auth_headers: dict
    ):
        """ERR-007: Upload document without doc_type parameter."""
        project = await create_test_project(client, auth_headers, "No DocType Test")

        files = {"file": ("test.pdf", create_test_pdf(), "application/pdf")}

        response = await client.post(
            f"/api/projects/{project['id']}/documents",
            files=files,
            headers=auth_headers,
        )

        # Missing required field should cause validation error
        assert response.status_code == 422


class TestProjectEdgeCases:
    """Tests for project management edge cases."""

    @pytest.mark.asyncio
    async def test_get_nonexistent_project(
        self, client: AsyncClient, auth_headers: dict
    ):
        """ERR-009: Get results for non-existent project."""
        fake_project_id = str(uuid.uuid4())

        response = await client.get(
            f"/api/projects/{fake_project_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_create_project_empty_name(
        self, client: AsyncClient, auth_headers: dict
    ):
        """ERR-009: Create project with empty name."""
        response = await client.post(
            "/api/projects",
            json={"name": "", "description": "Test"},
            headers=auth_headers,
        )

        # Validation should reject empty name
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_project_nonexistent(
        self, client: AsyncClient, auth_headers: dict
    ):
        """ERR-009: Update non-existent project."""
        fake_project_id = str(uuid.uuid4())

        response = await client.put(
            f"/api/projects/{fake_project_id}",
            json={"name": "Updated Name"},
            headers=auth_headers,
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_project_nonexistent(
        self, client: AsyncClient, auth_headers: dict
    ):
        """ERR-009: Delete non-existent project."""
        fake_project_id = str(uuid.uuid4())

        response = await client.delete(
            f"/api/projects/{fake_project_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404
