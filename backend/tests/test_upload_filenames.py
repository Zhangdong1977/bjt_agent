"""Upload filename validation and filesystem error mapping tests."""

import errno
import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from backend.api import documents as documents_api


PRODUCTION_LONG_FILENAME = (
    "招标文件（公开招标）发布稿（已盖章）2026.7.7第三批央企在京老旧小区综合整治项目"
    "（标包三百四十一）（海淀区学院路街道、海淀区中关村街道、海淀区紫竹院街道）"
    "工程总承包（EPC）.pdf"
)


def _upload(filename: str, data: bytes = b"%PDF-1.4") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data))


def test_storage_filename_remains_readable():
    storage_name = documents_api._build_storage_filename(
        "第三批央企标包341（EPC）.pdf",
        timestamp="20260727163856",
    )

    assert storage_name == "第三批央企标包341（EPC）_20260727163856.pdf"


def test_production_long_chinese_filename_returns_clear_error():
    with pytest.raises(HTTPException) as exc:
        documents_api._build_storage_filename(
            PRODUCTION_LONG_FILENAME,
            timestamp="20260727163856",
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == documents_api.FILENAME_TOO_LONG_DETAIL


def test_storage_filename_accepts_exact_255_byte_boundary():
    storage_name = documents_api._build_storage_filename(
        f"{'a' * 236}.pdf",
        timestamp="20260727163856",
    )

    assert len(storage_name.encode("utf-8")) == 255


def test_storage_filename_rejects_over_255_byte_boundary():
    with pytest.raises(HTTPException) as exc:
        documents_api._build_storage_filename(
            f"{'a' * 237}.pdf",
            timestamp="20260727163856",
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == documents_api.FILENAME_TOO_LONG_DETAIL


@pytest.mark.parametrize(
    "filename",
    [
        "目录/招标文件.pdf",
        "目录\\招标文件.pdf",
        "招标:文件.pdf",
        "招标?文件.pdf",
        "招标\x00文件.pdf",
        "招标\n文件.pdf",
        "CON.pdf",
        "招标文件 .pdf",
        "招标文件.pdf ",
    ],
)
def test_unsafe_filename_returns_clear_error(filename: str):
    with pytest.raises(HTTPException) as exc:
        documents_api._build_storage_filename(
            filename,
            timestamp="20260727163856",
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == documents_api.FILENAME_INVALID_DETAIL


@pytest.mark.asyncio
async def test_os_filename_too_long_error_is_mapped_to_400(monkeypatch, tmp_path: Path):
    async def fail_save(*_args, **_kwargs):
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(documents_api, "throttled_save", fail_save)

    with pytest.raises(HTTPException) as exc:
        await documents_api._save_upload_file(_upload("招标文件.pdf"), tmp_path)

    assert exc.value.status_code == 400
    assert exc.value.detail == documents_api.FILENAME_TOO_LONG_DETAIL


@pytest.mark.asyncio
async def test_non_filename_save_error_remains_server_error(monkeypatch, tmp_path: Path):
    async def fail_save(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(documents_api, "throttled_save", fail_save)

    with pytest.raises(OSError) as exc:
        await documents_api._save_upload_file(_upload("招标文件.pdf"), tmp_path)

    assert exc.value.errno == errno.ENOSPC


@pytest.mark.asyncio
async def test_project_upload_returns_long_filename_detail(client, auth_headers):
    project_response = await client.post(
        "/api/projects",
        json={"name": "Filename validation", "description": "Test"},
        headers=auth_headers,
    )
    project_id = project_response.json()["id"]

    response = await client.post(
        f"/api/projects/{project_id}/documents?doc_type=tender",
        files={
            "file": (
                PRODUCTION_LONG_FILENAME,
                io.BytesIO(b"%PDF-1.4"),
                "application/pdf",
            )
        },
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == documents_api.FILENAME_TOO_LONG_DETAIL


@pytest.mark.asyncio
async def test_draft_upload_returns_invalid_filename_detail(client, auth_headers):
    response = await client.post(
        "/api/documents/upload?doc_type=tender",
        files={"file": ("招标:文件.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == documents_api.FILENAME_INVALID_DETAIL
