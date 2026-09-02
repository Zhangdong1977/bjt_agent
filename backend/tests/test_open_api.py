"""Open API (/api/v1/open) tests.

覆盖：总开关 404、X-Api-Key 三态鉴权、/me 余额与限额、上传（source='api'
+ 不占 Web 草稿配额）、审查提交（隐式项目/client_channel='api'/幂等/402/409）、
查重 identical 拦截。
"""

import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.api.open import hash_api_key
from backend.config import get_settings
from backend.models import (
    ApiKey,
    Document,
    Project,
    ReviewTask,
    User,
    UserWallet,
    async_session_factory,
)
from backend.services.billing import ensure_wallet

OPEN_PREFIX = "/api/v1/open"


@pytest.fixture
def open_enabled(monkeypatch):
    """Enable the open channel (default off → 404)."""
    monkeypatch.setattr(get_settings(), "open_api_enabled", True)


@pytest.fixture
def no_parse_dispatch(monkeypatch):
    """Stub parse_document.delay so tests never touch the broker."""
    import backend.tasks.document_parser as parser_module

    monkeypatch.setattr(
        parser_module, "parse_document", SimpleNamespace(delay=lambda _id: None)
    )


@pytest.fixture
def no_task_dispatch(monkeypatch):
    """Stub outbox creation/delivery so tests never enqueue real work.

    本地 .env 指向预发布环境（VSTO 拓扑）：真实 outbox 行（status='pending'）
    会被预发布 celery beat 每 10s 扫描重投、worker 领走执行（fixture 文件
    不存在 → 快速失败），造成时序偶发失败。测试只验证 API 层语义：
    既不投递（dispatch_task_outbox），也不留 outbox 行（add_task_dispatch）。
    """
    import backend.services.task_lifecycle as lifecycle_module

    async def _noop_dispatch(outbox_id):
        return False

    def _fake_add_task_dispatch(db, *, task_kind, task_id):
        return SimpleNamespace(id=str(uuid.uuid4()), celery_task_id=str(uuid.uuid4()))

    monkeypatch.setattr(lifecycle_module, "dispatch_task_outbox", _noop_dispatch)
    monkeypatch.setattr(lifecycle_module, "add_task_dispatch", _fake_add_task_dispatch)


async def _make_key_user(*, max_active_tasks: int = 1, balance: Decimal = Decimal("100")):
    """Create a local user + ApiKey + funded wallet; returns (user_id, raw_key)."""
    raw_key = "bjt_live_test_" + uuid.uuid4().hex
    unique = uuid.uuid4().hex[:8]
    async with async_session_factory() as db:
        user = User(
            username=f"openapi_{unique}",
            email=f"openapi_{unique}@example.com",
            password_hash="placeholder",
        )
        db.add(user)
        await db.flush()
        db.add(
            ApiKey(
                user_id=user.id,
                name="test",
                key_prefix=raw_key[:12],
                key_hash=hash_api_key(raw_key),
                max_active_tasks=max_active_tasks,
            )
        )
        wallet = await ensure_wallet(db, user.id)
        wallet.recharge_balance_points = balance
        await db.commit()
        return user.id, raw_key


async def _make_parsed_doc(user_id: str, doc_type: str) -> str:
    async with async_session_factory() as db:
        document = Document(
            project_id=None,
            owner_user_id=user_id,
            doc_type=doc_type,
            original_filename=f"sample-{doc_type}.pdf",
            file_path=str(Path(__file__).parent / "fixtures" / "sample.pdf"),
            status="parsed",
            source="api",
        )
        db.add(document)
        await db.commit()
        return document.id


async def test_open_api_disabled_returns_404(client, unique_user):
    response = await client.get(f"{OPEN_PREFIX}/me", headers={"X-Api-Key": "whatever"})
    assert response.status_code == 404


async def test_me_auth_states(client, open_enabled):
    response = await client.get(f"{OPEN_PREFIX}/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "missing_credentials"

    response = await client.get(f"{OPEN_PREFIX}/me", headers={"X-Api-Key": "bjt_live_wrong"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"


async def test_me_returns_balance_and_limits(client, open_enabled):
    user_id, raw_key = await _make_key_user(max_active_tasks=3)
    response = await client.get(f"{OPEN_PREFIX}/me", headers={"X-Api-Key": raw_key})
    assert response.status_code == 200
    body = response.json()
    assert body["balance_points"] == 100.0
    assert body["recharge_points"] == 100.0
    assert body["gift_points"] == 0.0
    assert body["limits"]["max_active_tasks"] == 3
    assert body["limits"]["running_tasks"] == 0


async def test_upload_creates_api_document_not_web_draft(client, open_enabled, no_parse_dispatch):
    user_id, raw_key = await _make_key_user()
    response = await client.post(
        f"{OPEN_PREFIX}/documents",
        data={"doc_type": "tender"},
        files={"file": ("招标文件.pdf", b"%PDF-1.4 fake", "application/pdf")},
        headers={"X-Api-Key": raw_key},
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["document_id"]

    async with async_session_factory() as db:
        document = (
            await db.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()
        assert document.source == "api"
        assert document.owner_user_id == user_id
        assert document.project_id is None
        assert document.file_path  # saved under workspace _api dir

    # web 草稿列表不应出现 API 上传
    from backend.api.deps import create_access_token

    token = create_access_token(data={"sub": user_id, "interior_user": False, "concurrency": 2})
    drafts = await client.get(
        "/api/documents/drafts", headers={"Authorization": f"Bearer {token}"}
    )
    assert drafts.status_code == 200
    assert all(d["id"] != document_id for d in drafts.json()["documents"])


async def test_upload_rejects_bad_doc_type(client, open_enabled, no_parse_dispatch):
    _, raw_key = await _make_key_user()
    response = await client.post(
        f"{OPEN_PREFIX}/documents",
        data={"doc_type": "duplicate_bid"},
        files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        headers={"X-Api-Key": raw_key},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "validation_error"


async def test_review_submit_creates_api_task_with_idempotency(
    client, open_enabled, no_parse_dispatch, no_task_dispatch
):
    user_id, raw_key = await _make_key_user()
    tender_id = await _make_parsed_doc(user_id, "tender")
    bid_id = await _make_parsed_doc(user_id, "bid")

    headers = {"X-Api-Key": raw_key, "Idempotency-Key": str(uuid.uuid4())}
    response = await client.post(
        f"{OPEN_PREFIX}/review",
        json={"tender_document_ids": [tender_id], "bid_document_ids": [bid_id]},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["task_id"]

    async with async_session_factory() as db:
        task = (
            await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
        ).scalar_one()
        assert task.task_type == "review"
        assert task.client_channel == "api"
        assert task.status in ("pending", "running")
        project = (
            await db.execute(select(Project).where(Project.id == task.project_id))
        ).scalar_one()
        assert project.source == "api"
        assert project.project_type == "review"

    # 幂等：同 Idempotency-Key 重放返回同一 task_id
    replay = await client.post(
        f"{OPEN_PREFIX}/review",
        json={"tender_document_ids": [tender_id], "bid_document_ids": [bid_id]},
        headers=headers,
    )
    assert replay.status_code == 201
    assert replay.json()["task_id"] == task_id

    # 任务可见于开放通道列表
    listing = await client.get(f"{OPEN_PREFIX}/tasks", headers={"X-Api-Key": raw_key})
    assert listing.status_code == 200
    assert any(t["task_id"] == task_id for t in listing.json()["tasks"])

    # 状态端点返回契约字段
    status_response = await client.get(
        f"{OPEN_PREFIX}/tasks/{task_id}", headers={"X-Api-Key": raw_key}
    )
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["service"] == "review"
    assert body["status"] in ("pending", "running")


async def test_review_submit_402_when_balance_zero(client, open_enabled, no_parse_dispatch, no_task_dispatch):
    user_id, raw_key = await _make_key_user(balance=Decimal("0"))
    tender_id = await _make_parsed_doc(user_id, "tender")
    bid_id = await _make_parsed_doc(user_id, "bid")
    response = await client.post(
        f"{OPEN_PREFIX}/review",
        json={"tender_document_ids": [tender_id], "bid_document_ids": [bid_id]},
        headers={"X-Api-Key": raw_key},
    )
    assert response.status_code == 402
    assert response.json()["detail"]["code"] == "INSUFFICIENT_BALANCE"


async def test_review_submit_409_second_unsettled_task(client, open_enabled, no_parse_dispatch, no_task_dispatch):
    user_id, raw_key = await _make_key_user(max_active_tasks=1)
    tender_a = await _make_parsed_doc(user_id, "tender")
    bid_a = await _make_parsed_doc(user_id, "bid")
    first = await client.post(
        f"{OPEN_PREFIX}/review",
        json={"tender_document_ids": [tender_a], "bid_document_ids": [bid_a]},
        headers={"X-Api-Key": raw_key},
    )
    assert first.status_code == 201

    tender_b = await _make_parsed_doc(user_id, "tender")
    bid_b = await _make_parsed_doc(user_id, "bid")
    second = await client.post(
        f"{OPEN_PREFIX}/review",
        json={"tender_document_ids": [tender_b], "bid_document_ids": [bid_b]},
        headers={"X-Api-Key": raw_key},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "ACTIVE_BILLING_TASK_EXISTS"


async def test_review_submit_rejects_unparsed_document(client, open_enabled, no_parse_dispatch, no_task_dispatch):
    user_id, raw_key = await _make_key_user()
    tender_id = await _make_parsed_doc(user_id, "tender")
    async with async_session_factory() as db:
        pending_doc = Document(
            project_id=None,
            owner_user_id=user_id,
            doc_type="bid",
            original_filename="still-parsing.docx",
            file_path="unused",
            status="parsing",
            source="api",
        )
        db.add(pending_doc)
        await db.commit()
        pending_id = pending_doc.id
    response = await client.post(
        f"{OPEN_PREFIX}/review",
        json={"tender_document_ids": [tender_id], "bid_document_ids": [pending_id]},
        headers={"X-Api-Key": raw_key},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "validation_error"


async def test_duplicate_submit_identical_documents_rejected(
    client, open_enabled, no_parse_dispatch, no_task_dispatch, monkeypatch
):
    import backend.services.duplicate_hash as hash_module

    monkeypatch.setattr(
        hash_module, "find_identical_content_hash", lambda *a, **k: ("original", "digest")
    )
    user_id, raw_key = await _make_key_user()
    left_id = await _make_parsed_doc(user_id, "duplicate_left")
    right_id = await _make_parsed_doc(user_id, "duplicate_right")
    response = await client.post(
        f"{OPEN_PREFIX}/duplicate-check",
        json={"left_document_id": left_id, "right_document_id": right_id},
        headers={"X-Api-Key": raw_key},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "identical_documents"


async def test_duplicate_submit_same_id_rejected(client, open_enabled, no_parse_dispatch):
    user_id, raw_key = await _make_key_user()
    left_id = await _make_parsed_doc(user_id, "duplicate_left")
    response = await client.post(
        f"{OPEN_PREFIX}/duplicate-check",
        json={"left_document_id": left_id, "right_document_id": left_id},
        headers={"X-Api-Key": raw_key},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "identical_documents"


async def test_revoked_key_forbidden(client, open_enabled):
    from backend.utils.time_utils import utc_now

    user_id, raw_key = await _make_key_user()
    async with async_session_factory() as db:
        key_row = (
            await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw_key)))
        ).scalar_one()
        key_row.revoked_at = utc_now()
        await db.commit()
    response = await client.get(f"{OPEN_PREFIX}/me", headers={"X-Api-Key": raw_key})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "key_revoked"
