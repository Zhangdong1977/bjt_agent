"""Review-result sharing authorization and data-boundary tests."""

import asyncio
import shutil
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import delete, select

from backend.api.deps import create_access_token, get_password_hash
from backend.config import get_settings
from backend.models import (
    Project,
    ReviewResult,
    ReviewShareToken,
    ReviewTask,
    TodoItem,
    User,
    async_session_factory,
    engine,
)
from backend.utils.time_utils import utc_now


API = "/api/share"


def _headers(user: User) -> dict[str, str]:
    token = create_access_token(
        data={"sub": user.id, "interior_user": False, "concurrency": 2}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def share_case():
    """Create an isolated owner/viewer/project/task result and clean it up."""

    def ensure_share_schema(sync_conn) -> None:
        ReviewShareToken.__table__.create(sync_conn, checkfirst=True)
        # ``create(checkfirst=True)`` skips the whole table when it already
        # exists, so explicitly ensure indexes added by this feature revision.
        for index in ReviewShareToken.__table__.indexes:
            index.create(sync_conn, checkfirst=True)

    async with engine.begin() as conn:
        await conn.run_sync(ensure_share_schema)

    suffix = uuid.uuid4().hex[:10]
    async with async_session_factory() as session:
        owner = User(
            username=f"share_owner_{suffix}",
            email=f"share_owner_{suffix}@example.com",
            password_hash=get_password_hash("Test123!"),
        )
        viewer = User(
            username=f"share_viewer_{suffix}",
            email=f"share_viewer_{suffix}@example.com",
            password_hash=get_password_hash("Test123!"),
        )
        session.add_all([owner, viewer])
        await session.flush()

        project = Project(
            user_id=owner.id,
            name=f"分享测试项目_{suffix}",
            status="completed",
        )
        session.add(project)
        await session.flush()

        task = ReviewTask(project_id=project.id, status="completed")
        session.add(task)
        await session.flush()

        todo = TodoItem(
            project_id=project.id,
            session_id=task.id,
            rule_doc_path="/server/internal/rules/qualification.md",
            rule_doc_name="qualification.md",
            check_items=[{"id": "1", "title": "资格要求"}],
            status="completed",
            result={"report_path": "/server/internal/reports/review.md"},
        )
        finding = ReviewResult(
            task_id=task.id,
            requirement_key="qualification",
            requirement_content="须满足资格要求",
            bid_content="已满足",
            is_compliant=True,
            severity="minor",
            rule_doc_name="qualification.md",
            check_item_name="资格要求",
        )
        session.add_all([todo, finding])
        await session.commit()

    case = {
        "owner": owner,
        "viewer": viewer,
        "owner_headers": _headers(owner),
        "viewer_headers": _headers(viewer),
        "project_id": project.id,
        "task_id": task.id,
        "todo_id": todo.id,
    }
    try:
        yield case
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(ReviewShareToken).where(
                    ReviewShareToken.project_id == project.id
                )
            )
            await session.execute(delete(ReviewResult).where(ReviewResult.task_id == task.id))
            await session.execute(delete(TodoItem).where(TodoItem.session_id == task.id))
            await session.execute(delete(ReviewTask).where(ReviewTask.id == task.id))
            await session.execute(delete(Project).where(Project.id == project.id))
            await session.execute(delete(User).where(User.id.in_([owner.id, viewer.id])))
            await session.commit()
        await engine.dispose()


async def _create_share(client: AsyncClient, case: dict) -> dict:
    response = await client.post(
        f"{API}/projects/{case['project_id']}/tasks/{case['task_id']}",
        headers=case["owner_headers"],
        json={},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_share_requires_owner_and_returns_sanitized_read_only_payload(
    client: AsyncClient, share_case: dict
):
    case = share_case
    create_url = f"{API}/projects/{case['project_id']}/tasks/{case['task_id']}"

    denied = await client.post(create_url, headers=case["viewer_headers"], json={})
    assert denied.status_code == 404

    info = await _create_share(client, case)
    # Reopening the modal reuses the one active URL.
    reused = await _create_share(client, case)
    assert reused["token"] == info["token"]

    unauthenticated = await client.get(f"{API}/{info['token']}")
    assert unauthenticated.status_code == 401

    shared = await client.get(
        f"{API}/{info['token']}", headers=case["viewer_headers"]
    )
    assert shared.status_code == 200, shared.text
    body = shared.json()
    assert body["project_id"] == case["project_id"]
    assert len(body["findings"]) == 1
    assert len(body["todos"]) == 1
    assert set(body["todos"][0]) == {
        "id",
        "rule_doc_name",
        "check_items",
        "status",
        "created_at",
    }
    assert "rule_doc_path" not in body["todos"][0]
    assert "result" not in body["todos"][0]

    denied_revoke = await client.delete(
        f"{API}/{info['token']}", headers=case["viewer_headers"]
    )
    assert denied_revoke.status_code == 404

    revoked = await client.delete(
        f"{API}/{info['token']}", headers=case["owner_headers"]
    )
    assert revoked.status_code == 200
    after_revoke = await client.get(
        f"{API}/{info['token']}", headers=case["viewer_headers"]
    )
    assert after_revoke.status_code == 404


@pytest.mark.asyncio
async def test_expired_active_token_is_retired_and_replaced(
    client: AsyncClient, share_case: dict
):
    case = share_case
    first = await _create_share(client, case)

    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(ReviewShareToken).where(
                    ReviewShareToken.token == first["token"]
                )
            )
        ).scalar_one()
        row.expires_at = utc_now() - timedelta(minutes=1)
        await session.commit()

    second = await _create_share(client, case)
    assert second["token"] != first["token"]

    async with async_session_factory() as session:
        old_row = (
            await session.execute(
                select(ReviewShareToken).where(
                    ReviewShareToken.token == first["token"]
                )
            )
        ).scalar_one()
        assert old_row.is_active is False


@pytest.mark.asyncio
async def test_concurrent_create_reuses_one_active_token(
    client: AsyncClient, share_case: dict
):
    case = share_case
    first, second = await asyncio.gather(
        _create_share(client, case),
        _create_share(client, case),
    )
    assert first["token"] == second["token"]

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(ReviewShareToken).where(
                    ReviewShareToken.task_id == case["task_id"],
                    ReviewShareToken.is_active.is_(True),
                )
            )
        ).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_soft_deleted_project_invalidates_share(
    client: AsyncClient, share_case: dict
):
    case = share_case
    info = await _create_share(client, case)

    async with async_session_factory() as session:
        project = await session.get(Project, case["project_id"])
        project.is_deleted = True
        project.deleted_at = utc_now()
        await session.commit()

    response = await client.get(
        f"{API}/{info['token']}", headers=case["viewer_headers"]
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_shared_report_must_stay_inside_owner_project_workspace(
    client: AsyncClient, share_case: dict, tmp_path
):
    case = share_case
    info = await _create_share(client, case)
    report_url = f"{API}/{info['token']}/report/{case['todo_id']}"

    outside = tmp_path / "outside.md"
    outside.write_text("outside secret", encoding="utf-8")
    async with async_session_factory() as session:
        todo = await session.get(TodoItem, case["todo_id"])
        todo.result = {"report_path": str(outside)}
        await session.commit()

    rejected = await client.get(report_url, headers=case["viewer_headers"])
    assert rejected.status_code == 404

    workspace = (
        get_settings().workspace_path
        / case["owner"].id
        / case["project_id"]
    )
    workspace.mkdir(parents=True, exist_ok=True)
    report = workspace / "review_test.md"
    report.write_text("# 可分享报告", encoding="utf-8")
    try:
        async with async_session_factory() as session:
            todo = await session.get(TodoItem, case["todo_id"])
            todo.result = {"report_path": str(report)}
            await session.commit()

        accepted = await client.get(report_url, headers=case["viewer_headers"])
        assert accepted.status_code == 200, accepted.text
        assert accepted.text == "# 可分享报告"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
