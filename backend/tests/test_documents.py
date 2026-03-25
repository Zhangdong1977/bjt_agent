"""Document management module tests.

Test cases:
- DOC-001: Upload PDF tender document
- DOC-002: Upload DOCX bid document
- DOC-003: Upload unsupported file type
- DOC-004: Document parsing status
- DOC-005: Get document content
- DOC-006: Get content when not parsed
- DOC-007: Delete document
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
    # Minimal PDF content
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


def create_test_docx() -> io.BytesIO:
    """Create a minimal DOCX file for testing (ZIP archive)."""
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        # Add minimal content types
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        # Add minimal document
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Test Document Content</w:t></w:r></w:p></w:body></w:document>',
        )
        # Add relationships
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )

    buffer.seek(0)
    return buffer


class TestDocumentUpload:
    """Tests for document upload endpoint."""

    @pytest.mark.asyncio
    async def test_upload_pdf_tender(self, client: AsyncClient, auth_headers: dict):
        """DOC-001: Upload PDF tender document."""
        project = await create_test_project(client, auth_headers, "Upload Test Project")

        files = {"file": ("test_tender.pdf", create_test_pdf(), "application/pdf")}
        data = {"doc_type": "tender"}

        response = await client.post(
            f"/api/projects/{project['id']}/documents",
            files=files,
            data=data,
            headers=auth_headers,
        )

        assert response.status_code == 201
        doc = response.json()
        assert "id" in doc
        assert doc["doc_type"] == "tender"
        assert doc["status"] == "pending"
        assert doc["original_filename"] == "test_tender.pdf"

    @pytest.mark.asyncio
    async def test_upload_docx_bid(self, client: AsyncClient, auth_headers: dict):
        """DOC-002: Upload DOCX bid document."""
        project = await create_test_project(client, auth_headers, "Upload DOCX Test")

        files = {"file": ("bid_proposal.docx", create_test_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        data = {"doc_type": "bid"}

        response = await client.post(
            f"/api/projects/{project['id']}/documents",
            files=files,
            data=data,
            headers=auth_headers,
        )

        assert response.status_code == 201
        doc = response.json()
        assert doc["doc_type"] == "bid"
        assert doc["status"] == "pending"

    @pytest.mark.asyncio
    async def test_upload_unsupported_format(self, client: AsyncClient, auth_headers: dict):
        """DOC-003: Upload unsupported file type."""
        project = await create_test_project(client, auth_headers, "Unsupported Test")

        # Create a text file
        txt_content = b"This is a plain text file"
        files = {"file": ("readme.txt", io.BytesIO(txt_content), "text/plain")}
        data = {"doc_type": "tender"}

        response = await client.post(
            f"/api/projects/{project['id']}/documents",
            files=files,
            data=data,
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "Unsupported file type" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_upload_invalid_doc_type(self, client: AsyncClient, auth_headers: dict):
        """DOC-003: Upload with invalid doc_type."""
        project = await create_test_project(client, auth_headers, "Invalid DocType Test")

        files = {"file": ("test.pdf", create_test_pdf(), "application/pdf")}
        data = {"doc_type": "invalid_type"}

        response = await client.post(
            f"/api/projects/{project['id']}/documents",
            files=files,
            data=data,
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "doc_type must be 'tender' or 'bid'" in response.json()["detail"]


class TestDocumentList:
    """Tests for document listing."""

    @pytest.mark.asyncio
    async def test_list_documents(self, client: AsyncClient, auth_headers: dict):
        """DOC-001 variant: List documents in a project."""
        project = await create_test_project(client, auth_headers, "List Docs Test")

        # Upload a document
        files = {"file": ("tender.pdf", create_test_pdf(), "application/pdf")}
        data = {"doc_type": "tender"}
        await client.post(
            f"/api/projects/{project['id']}/documents",
            files=files,
            data=data,
            headers=auth_headers,
        )

        response = await client.get(
            f"/api/projects/{project['id']}/documents",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "documents" in data
        assert len(data["documents"]) >= 1


class TestDocumentGet:
    """Tests for getting document details."""

    @pytest.mark.asyncio
    async def test_get_document(self, client: AsyncClient, auth_headers: dict):
        """DOC-001 variant: Get document details."""
        project = await create_test_project(client, auth_headers, "Get Doc Test")

        # Upload a document
        files = {"file": ("tender.pdf", create_test_pdf(), "application/pdf")}
        data = {"doc_type": "tender"}
        upload_response = await client.post(
            f"/api/projects/{project['id']}/documents",
            files=files,
            data=data,
            headers=auth_headers,
        )
        doc_id = upload_response.json()["id"]

        response = await client.get(
            f"/api/projects/{project['id']}/documents/{doc_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        doc = response.json()
        assert doc["id"] == doc_id
        assert doc["doc_type"] == "tender"


class TestDocumentContent:
    """Tests for getting document content."""

    @pytest.mark.asyncio
    async def test_get_content_not_parsed(self, client: AsyncClient, auth_headers: dict):
        """DOC-006: Get content when document is not parsed."""
        project = await create_test_project(client, auth_headers, "Content Test")

        # Upload a document (status will be 'pending')
        files = {"file": ("tender.pdf", create_test_pdf(), "application/pdf")}
        data = {"doc_type": "tender"}
        upload_response = await client.post(
            f"/api/projects/{project['id']}/documents",
            files=files,
            data=data,
            headers=auth_headers,
        )
        doc_id = upload_response.json()["id"]

        # Try to get content (should fail as status is 'pending')
        response = await client.get(
            f"/api/projects/{project['id']}/documents/{doc_id}/content",
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "Document is not parsed yet" in response.json()["detail"]


class TestDocumentDelete:
    """Tests for document deletion."""

    @pytest.mark.asyncio
    async def test_delete_document(self, client: AsyncClient, auth_headers: dict):
        """DOC-007: Delete a document."""
        project = await create_test_project(client, auth_headers, "Delete Doc Test")

        # Upload a document
        files = {"file": ("tender.pdf", create_test_pdf(), "application/pdf")}
        data = {"doc_type": "tender"}
        upload_response = await client.post(
            f"/api/projects/{project['id']}/documents",
            files=files,
            data=data,
            headers=auth_headers,
        )
        doc_id = upload_response.json()["id"]

        # Delete the document
        response = await client.delete(
            f"/api/projects/{project['id']}/documents/{doc_id}",
            headers=auth_headers,
        )

        assert response.status_code == 204

        # Verify deletion
        get_response = await client.get(
            f"/api/projects/{project['id']}/documents/{doc_id}",
            headers=auth_headers,
        )
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_document_not_found(
        self, client: AsyncClient, auth_headers: dict
    ):
        """DOC-007: Delete non-existent document."""
        project = await create_test_project(client, auth_headers, "Delete NonExistent")

        fake_doc_id = str(uuid.uuid4())
        response = await client.delete(
            f"/api/projects/{project['id']}/documents/{fake_doc_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404
