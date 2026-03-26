"""Integration tests for full workflow scenarios.

Test cases:
- INT-001: Full document review flow (upload → parse → review → results)
- INT-002: Project lifecycle (create → add documents → review → delete)
- INT-003: Multi-user isolation (users cannot access each other's data)
- INT-004: Concurrent review requests are rejected
- INT-005: Document parsing updates document status correctly
"""

import io
import uuid
import time
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


def create_test_pdf(content: str = "Test Document") -> io.BytesIO:
    """Create a minimal PDF file for testing."""
    pdf_content = f"""%PDF-1.4
1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj
2 0 obj<</Type /Pages /Kids [3 0 R] /Count 1>>endobj
3 0 obj<</Type /Page /Parent 2 0 R /Resources 4 0 R /MediaBox [0 0 612 792] /Contents 5 0 R>>endobj
4 0 obj<</Font<</F1 6 0 R>>>>endobj
5 0 obj<</Length 44>>stream
BT
/F1 12 Tf
100 700 Td
({content}) Tj
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
%%EOF""".encode()
    return io.BytesIO(pdf_content)


def create_test_docx(content: str = "Test Document Content") -> io.BytesIO:
    """Create a minimal DOCX file for testing (ZIP archive)."""
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{content}</w:t></w:r></w:p></w:body></w:document>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )

    buffer.seek(0)
    return buffer


class TestFullWorkflow:
    """Integration tests for complete workflows."""

    @pytest.mark.asyncio
    async def test_project_lifecycle(
        self, client: AsyncClient, auth_headers: dict
    ):
        """INT-002: Project lifecycle - create → add documents → review → delete."""
        # 1. Create project
        project = await create_test_project(
            client, auth_headers, "Lifecycle Test Project"
        )
        assert "id" in project
        project_id = project["id"]

        # 2. Upload tender document
        tender_files = {
            "file": ("tender.pdf", create_test_pdf("Tender Requirements"), "application/pdf")
        }
        tender_response = await client.post(
            f"/api/projects/{project_id}/documents?doc_type=tender",
            files=tender_files,
            headers=auth_headers,
        )
        assert tender_response.status_code == 201
        tender_doc = tender_response.json()

        # 3. Upload bid document
        bid_files = {
            "file": ("bid.docx", create_test_docx("Bid Proposal Content"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        }
        bid_response = await client.post(
            f"/api/projects/{project_id}/documents?doc_type=bid",
            files=bid_files,
            headers=auth_headers,
        )
        assert bid_response.status_code == 201
        bid_doc = bid_response.json()

        # 4. List documents
        list_response = await client.get(
            f"/api/projects/{project_id}/documents",
            headers=auth_headers,
        )
        assert list_response.status_code == 200
        docs = list_response.json()["documents"]
        assert len(docs) == 2

        # 5. Start review
        review_response = await client.post(
            f"/api/projects/{project_id}/review",
            headers=auth_headers,
        )
        assert review_response.status_code == 201
        task = review_response.json()
        assert task["project_id"] == project_id
        assert task["status"] == "pending"

        # 6. Get task status
        status_response = await client.get(
            f"/api/projects/{project_id}/review/tasks/{task['id']}",
            headers=auth_headers,
        )
        assert status_response.status_code == 200

        # 7. Get review results (may be empty if not yet completed)
        results_response = await client.get(
            f"/api/projects/{project_id}/review",
            headers=auth_headers,
        )
        assert results_response.status_code == 200
        results = results_response.json()
        assert "summary" in results
        assert "findings" in results

        # 8. Cancel the task
        cancel_response = await client.post(
            f"/api/projects/{project_id}/review/tasks/{task['id']}/cancel",
            headers=auth_headers,
        )
        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == "cancelled"

        # 9. Delete project
        delete_response = await client.delete(
            f"/api/projects/{project_id}",
            headers=auth_headers,
        )
        assert delete_response.status_code == 204

        # 10. Verify project is deleted
        get_response = await client.get(
            f"/api/projects/{project_id}",
            headers=auth_headers,
        )
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_multi_user_isolation(
        self, client: AsyncClient, auth_headers: dict
    ):
        """INT-003: Multi-user data isolation."""
        # User A creates project and uploads document
        project_a = await create_test_project(
            client, auth_headers, "User A Project"
        )
        project_id_a = project_a["id"]

        files = {
            "file": ("tender.pdf", create_test_pdf(), "application/pdf")
        }
        await client.post(
            f"/api/projects/{project_id_a}/documents?doc_type=tender",
            files=files,
            headers=auth_headers,
        )

        # User B creates their own project
        username_b = f"user_b_{uuid.uuid4().hex[:8]}"
        email_b = f"{username_b}@example.com"
        password_b = "Test123!"

        await client.post(
            "/api/auth/register",
            json={"username": username_b, "email": email_b, "password": password_b},
        )

        login_response = await client.post(
            "/api/auth/login",
            data={"username": username_b, "password": password_b},
        )
        user_b_token = login_response.json()["access_token"]
        user_b_headers = {"Authorization": f"Bearer {user_b_token}"}

        project_b = await create_test_project(
            client, user_b_headers, "User B Project"
        )

        # User B can see their own project
        my_project_response = await client.get(
            f"/api/projects/{project_b['id']}",
            headers=user_b_headers,
        )
        assert my_project_response.status_code == 200

        # User B cannot see User A's project
        cross_project_response = await client.get(
            f"/api/projects/{project_id_a}",
            headers=user_b_headers,
        )
        assert cross_project_response.status_code == 404

        # User B cannot see User A's documents
        cross_docs_response = await client.get(
            f"/api/projects/{project_id_a}/documents",
            headers=user_b_headers,
        )
        assert cross_docs_response.status_code == 404

        # User B cannot start review on User A's project
        cross_review_response = await client.post(
            f"/api/projects/{project_id_a}/review",
            headers=user_b_headers,
        )
        assert cross_review_response.status_code == 404

        # User A cannot see User B's project
        cross_project_response_a = await client.get(
            f"/api/projects/{project_b['id']}",
            headers=auth_headers,
        )
        assert cross_project_response_a.status_code == 404

    @pytest.mark.asyncio
    async def test_concurrent_review_rejection(
        self, client: AsyncClient, auth_headers: dict
    ):
        """INT-004: Concurrent review requests are rejected."""
        project = await create_test_project(
            client, auth_headers, "Concurrent Review Test"
        )

        # Start first review
        response1 = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        assert response1.status_code == 201
        task1_id = response1.json()["id"]

        # Try to start second review - should be rejected
        response2 = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        assert response2.status_code == 400
        assert "already running" in response2.json()["detail"]

        # Cancel first review
        await client.post(
            f"/api/projects/{project['id']}/review/tasks/{task1_id}/cancel",
            headers=auth_headers,
        )

        # Now second review should succeed
        response3 = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )
        assert response3.status_code == 201

    @pytest.mark.asyncio
    async def test_review_task_creation_with_documents(
        self, client: AsyncClient, auth_headers: dict
    ):
        """INT-001: Verify review task is created correctly when documents exist."""
        project = await create_test_project(
            client, auth_headers, "Review With Docs Test"
        )

        # Upload both documents
        await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files={"file": ("tender.pdf", create_test_pdf("Requirements"), "application/pdf")},
            headers=auth_headers,
        )

        await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=bid",
            files={"file": ("bid.docx", create_test_docx("Bid Content"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            headers=auth_headers,
        )

        # Start review
        review_response = await client.post(
            f"/api/projects/{project['id']}/review",
            headers=auth_headers,
        )

        assert review_response.status_code == 201
        task = review_response.json()
        assert task["project_id"] == project["id"]
        assert task["status"] == "pending"
        assert "id" in task

        # Get task steps - should start empty
        steps_response = await client.get(
            f"/api/projects/{project['id']}/review/tasks/{task['id']}/steps",
            headers=auth_headers,
        )
        assert steps_response.status_code == 200
        assert isinstance(steps_response.json(), list)


class TestDocumentStatusTransitions:
    """Tests for document status transitions during parsing."""

    @pytest.mark.asyncio
    async def test_document_status_after_upload(
        self, client: AsyncClient, auth_headers: dict
    ):
        """INT-005: Document status is 'pending' after upload."""
        project = await create_test_project(
            client, auth_headers, "Document Status Test"
        )

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files={"file": ("tender.pdf", create_test_pdf(), "application/pdf")},
            headers=auth_headers,
        )

        assert response.status_code == 201
        doc = response.json()
        assert doc["status"] == "pending"
        assert doc["doc_type"] == "tender"

    @pytest.mark.asyncio
    async def test_list_documents_returns_correct_structure(
        self, client: AsyncClient, auth_headers: dict
    ):
        """INT-005: List documents returns correct structure."""
        project = await create_test_project(
            client, auth_headers, "List Docs Structure Test"
        )

        # Upload documents
        await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files={"file": ("tender.pdf", create_test_pdf(), "application/pdf")},
            headers=auth_headers,
        )

        await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=bid",
            files={"file": ("bid.docx", create_test_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            headers=auth_headers,
        )

        response = await client.get(
            f"/api/projects/{project['id']}/documents",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "documents" in data
        assert len(data["documents"]) == 2

        # Check document structure
        for doc in data["documents"]:
            assert "id" in doc
            assert "doc_type" in doc
            assert "status" in doc
            assert "original_filename" in doc
            assert "created_at" in doc


class TestAuthenticationFlows:
    """Tests for authentication-related flows."""

    @pytest.mark.asyncio
    async def test_login_then_access_protected_resource(
        self, client: AsyncClient
    ):
        """INT-003: Login and then access protected resource."""
        # Register
        username = f"test_user_{uuid.uuid4().hex[:8]}"
        email = f"{username}@example.com"
        password = "Test123!"

        await client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )

        # Login
        login_response = await client.post(
            "/api/auth/login",
            data={"username": username, "password": password},
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]

        # Access protected resource
        headers = {"Authorization": f"Bearer {token}"}
        projects_response = await client.get("/api/projects", headers=headers)
        assert projects_response.status_code == 200

    @pytest.mark.asyncio
    async def test_refresh_token_flow(
        self, client: AsyncClient
    ):
        """INT-003: Token refresh flow works correctly."""
        # Register and login
        username = f"test_user_{uuid.uuid4().hex[:8]}"
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
        refresh_token = login_response.json()["refresh_token"]

        # Use refresh token
        refresh_response = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh_response.status_code == 200
        new_access_token = refresh_response.json()["access_token"]

        # Use new access token
        headers = {"Authorization": f"Bearer {new_access_token}"}
        me_response = await client.get("/api/auth/me", headers=headers)
        assert me_response.status_code == 200
