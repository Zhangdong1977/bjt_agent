"""Tests for file upload size limit validation.

Test cases:
- UPLOAD-001: Upload file within size limit should succeed
- UPLOAD-002: Upload file exceeding size limit should return 413
- UPLOAD-003: Upload file exactly at size limit should succeed
- UPLOAD-004: Configurable max file size via settings
- UPLOAD-005: Empty file should be rejected (too small)
- UPLOAD-006: File size validation happens before parsing
"""

import io
import pytest
from httpx import AsyncClient


# Default max file size in settings (10MB)
DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def create_test_file(size_bytes: int, content: bytes = b"x") -> io.BytesIO:
    """Create a test file of specified size.

    Args:
        size_bytes: Target file size in bytes
        content: Content to repeat to reach target size

    Returns:
        BytesIO object with file content
    """
    content_repeated = content * (size_bytes // len(content) + 1)
    return io.BytesIO(content_repeated[:size_bytes])


async def create_test_project(
    client: AsyncClient,
    auth_headers: dict,
    name: str = "Upload Size Test Project",
) -> dict:
    """Helper to create a test project."""
    response = await client.post(
        "/api/projects",
        json={"name": name, "description": "Test"},
        headers=auth_headers,
    )
    return response.json()


class TestFileUploadSizeLimit:
    """Tests for file upload size limit validation."""

    @pytest.mark.asyncio
    async def test_upload_file_within_limit(
        self, client: AsyncClient, auth_headers: dict
    ):
        """UPLOAD-001: Upload file within size limit should succeed.

        A file smaller than the max size limit should be uploaded successfully.
        """
        project = await create_test_project(client, auth_headers)

        # Create a 1MB file (well under 10MB limit)
        file_content = create_test_file(1 * 1024 * 1024)  # 1 MB
        files = {"file": ("test_tender.pdf", file_content, "application/pdf")}

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files=files,
            headers=auth_headers,
        )

        assert response.status_code == 201
        doc = response.json()
        assert doc["status"] == "pending"

    @pytest.mark.asyncio
    async def test_upload_file_exceeding_limit(
        self, client: AsyncClient, auth_headers: dict
    ):
        """UPLOAD-002: Upload file exceeding size limit should return 413.

        A file larger than the max size limit should be rejected with
        HTTP 413 Request Entity Too Large.
        """
        project = await create_test_project(client, auth_headers)

        # Create a 15MB file (over 10MB limit)
        file_content = create_test_file(15 * 1024 * 1024)  # 15 MB
        files = {"file": ("large_tender.pdf", file_content, "application/pdf")}

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files=files,
            headers=auth_headers,
        )

        assert response.status_code == 413
        assert "size" in response.json()["detail"].lower() or "large" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_upload_file_at_limit(
        self, client: AsyncClient, auth_headers: dict
    ):
        """UPLOAD-003: Upload file exactly at size limit should succeed.

        A file exactly equal to the max size limit should be accepted.
        """
        project = await create_test_project(client, auth_headers)

        # Create exactly 10MB file
        file_content = create_test_file(DEFAULT_MAX_FILE_SIZE)
        files = {"file": ("exact_limit.pdf", file_content, "application/pdf")}

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files=files,
            headers=auth_headers,
        )

        # Should succeed (exact limit is acceptable)
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_upload_file_slightly_over_limit(
        self, client: AsyncClient, auth_headers: dict
    ):
        """UPLOAD-002 variant: Upload file slightly over limit should fail.

        A file just over the limit (e.g., 10MB + 1KB) should be rejected.
        """
        project = await create_test_project(client, auth_headers)

        # Create a 10MB + 1KB file
        file_content = create_test_file(DEFAULT_MAX_FILE_SIZE + 1024)
        files = {"file": ("over_limit.pdf", file_content, "application/pdf")}

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files=files,
            headers=auth_headers,
        )

        assert response.status_code == 413

    @pytest.mark.asyncio
    async def test_upload_empty_file(
        self, client: AsyncClient, auth_headers: dict
    ):
        """UPLOAD-005: Upload empty file should be rejected.

        An empty file (0 bytes) should be rejected as invalid.
        """
        project = await create_test_project(client, auth_headers)

        empty_content = b""
        files = {"file": ("empty.pdf", io.BytesIO(empty_content), "application/pdf")}

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files=files,
            headers=auth_headers,
        )

        # Empty file should be rejected (either 400 or 413)
        assert response.status_code in [400, 413]

    @pytest.mark.asyncio
    async def test_upload_docx_within_limit(
        self, client: AsyncClient, auth_headers: dict
    ):
        """UPLOAD-001 variant: Upload DOCX within size limit should succeed.

        The size limit applies to all supported file types.
        """
        import zipfile

        project = await create_test_project(client, auth_headers)

        # Create a minimal DOCX file (well under limit)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            )
            zf.writestr(
                "word/document.xml",
                '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Test</w:t></w:r></w:p></w:body></w:document>',
            )
        buffer.seek(0)

        files = {
            "file": (
                "test_doc.docx",
                buffer,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        }

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=bid",
            files=files,
            headers=auth_headers,
        )

        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_upload_docx_exceeding_limit(
        self, client: AsyncClient, auth_headers: dict
    ):
        """UPLOAD-002 variant: Upload DOCX exceeding size limit should return 413.

        Size limit applies to DOCX files as well.
        """
        import zipfile

        project = await create_test_project(client, auth_headers)

        # Create a large DOCX file (over limit)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            # Add lots of content to exceed limit
            content = "x" * (15 * 1024 * 1024)  # 15 MB of data
            zf.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            )
            zf.writestr("word/document.xml", f"<document>{content}</document>")
        buffer.seek(0)

        files = {
            "file": (
                "large_doc.docx",
                buffer,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        }

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=bid",
            files=files,
            headers=auth_headers,
        )

        assert response.status_code == 413


class TestFileSizeValidationTiming:
    """Tests to verify file size validation happens at the right time."""

    @pytest.mark.asyncio
    async def test_size_validation_before_extension_check(
        self, client: AsyncClient, auth_headers: dict
    ):
        """UPLOAD-006: Size validation should happen before extension validation.

        A file that exceeds size limit should fail with size error,
        not extension error, even if the extension is invalid.
        """
        project = await create_test_project(client, auth_headers)

        # Create a large file with invalid extension
        large_content = b"x" * (15 * 1024 * 1024)  # 15 MB
        files = {"file": ("test.exe", io.BytesIO(large_content), "application/x-executable")}

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files=files,
            headers=auth_headers,
        )

        # Should fail with 413 (size) not 400 (extension)
        assert response.status_code == 413

    @pytest.mark.asyncio
    async def test_size_validation_before_upload_complete(
        self, client: AsyncClient, auth_headers: dict
    ):
        """UPLOAD-006: Size validation should happen early in the upload process.

        The server should reject oversized files before fully receiving them.
        This is tested by verifying the response is 413, not 500.
        """
        project = await create_test_project(client, auth_headers)

        # Create a file that exceeds limit
        large_content = b"x" * (20 * 1024 * 1024)  # 20 MB
        files = {"file": ("large.pdf", io.BytesIO(large_content), "application/pdf")}

        response = await client.post(
            f"/api/projects/{project['id']}/documents?doc_type=tender",
            files=files,
            headers=auth_headers,
        )

        # Should get a proper 413 error, not server error
        assert response.status_code == 413
