"""系统状态端点 + 维护服务集成测试。

连预发布库（``backend/.env`` 的 DATABASE_URL = 10.0.3.23/bjt_agent）。
``system_maintenance`` 表由 ``ensure_schema`` 经 ``init_db()`` 幂等创建
（等价于 migration 020）；每个用例前后恢复 maintenance 单行，避免残留
影响预发布库上的其他使用者。

运行：D:\\miniconda3\\envs\\bjt-agent\\python.exe -m pytest backend/tests/test_system_status_api.py -v
"""

import pytest
import pytest_asyncio

from backend.models import async_session_factory, engine, init_db
from backend.services.maintenance_service import (
    ensure_maintenance_row,
    get_maintenance_state,
    set_maintenance_state,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def ensure_schema():
    """幂等建表 + 单行种子（等价 migration 020 + lifespan ensure）。

    function 级：与 function 级测试的 event-loop scope 对齐（pytest-asyncio 1.x
    严格校验 loop_scope，session 级 async fixture 会与 function 级测试冲突）。
    init_db 是 create_all 幂等的，每用例重跑无副作用。
    """
    await init_db()
    async with async_session_factory() as db:
        await ensure_maintenance_row(db)


@pytest_asyncio.fixture(autouse=True)
async def restore_maintenance():
    """用例前快照、用例后恢复，保证预发布库不被测试残留置为维护中。"""
    async with async_session_factory() as db:
        before = await get_maintenance_state(db)
    yield
    async with async_session_factory() as db:
        await set_maintenance_state(
            db,
            enabled=before.is_enabled,
            reason=before.reason,
            user_id="test-restore",
        )
    # 模块级 engine + function 级 event loop：用例间必须释放连接池，否则
    # asyncpg 连接绑在已关闭的上一个 loop 上，下一个用例报 "Event loop is closed"。
    await engine.dispose()


# ---------------------------------------------------------------------------
# 公开端点
# ---------------------------------------------------------------------------


async def test_public_maintenance_no_auth(client):
    resp = await client.get("/api/system-status/maintenance")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "is_enabled" in data and isinstance(data["is_enabled"], bool)
    assert "reason" in data and "started_at" in data


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------


async def test_full_status_forbidden_for_non_interior(client, auth_headers):
    resp = await client.get("/api/system-status", headers=auth_headers)
    assert resp.status_code == 403


async def test_toggle_forbidden_for_non_interior(client, auth_headers):
    resp = await client.post(
        "/api/system-status/maintenance",
        headers=auth_headers,
        json={"enabled": True, "reason": "x"},
    )
    assert resp.status_code == 403


async def test_full_status_ok_for_interior(client, interior_auth_headers):
    resp = await client.get("/api/system-status", headers=interior_auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for key in ("maintenance", "overview", "nodes", "workers", "queue_depths"):
        assert key in data, f"missing {key}"
    assert "is_enabled" in data["maintenance"]
    assert "alive_workers" in data["overview"]
    assert "running_reviews" in data["overview"]


# ---------------------------------------------------------------------------
# 维护切换往返（必然恢复为 disabled）
# ---------------------------------------------------------------------------


async def test_toggle_round_trip(client, interior_auth_headers):
    # 开启
    resp = await client.post(
        "/api/system-status/maintenance",
        headers=interior_auth_headers,
        json={"enabled": True, "reason": "测试-开启"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_enabled"] is True

    # 公开端点立即反映
    pub = (await client.get("/api/system-status/maintenance")).json()
    assert pub["is_enabled"] is True
    assert "测试-开启" in pub["reason"]

    # 关闭
    resp = await client.post(
        "/api/system-status/maintenance",
        headers=interior_auth_headers,
        json={"enabled": False, "reason": ""},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_enabled"] is False


async def test_maintenance_state_read_fail_open():
    """get_maintenance_state 在行存在时返回实际值（这里行已由 ensure_schema 建好）。"""
    async with async_session_factory() as db:
        state = await get_maintenance_state(db)
    assert isinstance(state.is_enabled, bool)
    assert state.reason is not None  # 即便空串也是 str
