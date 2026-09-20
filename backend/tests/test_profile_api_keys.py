"""Profile API key management (E1) tests.

个人中心「API Key」页签的后端端点：列表（只回前缀）、生成（明文一次性返回、
上限 3 个）、吊销（立即失效）；并与开放通道（/api/v1/open）联动验证。
"""

import json

import pytest

from backend.config import get_settings

OPEN_PREFIX = "/api/v1/open"


@pytest.fixture
def open_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "open_api_enabled", True)


async def test_api_key_lifecycle(client, auth_headers, open_enabled):
    # 初始为空
    response = await client.get("/api/profile/api-keys", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []

    # 生成：明文一次性返回
    response = await client.post(
        "/api/profile/api-keys", json={"name": "workbuddy-skill"}, headers=auth_headers
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["api_key"].startswith("bjt_live_")
    assert body["name"] == "workbuddy-skill"
    key_id = body["id"]
    raw_key = body["api_key"]

    # 列表只回前缀，绝不回明文
    response = await client.get("/api/profile/api-keys", headers=auth_headers)
    items = response.json()
    assert len(items) == 1
    assert items[0]["key_prefix"]
    assert raw_key not in json.dumps(items)

    # 新 key 可直接走开放通道
    response = await client.get(f"{OPEN_PREFIX}/me", headers={"X-Api-Key": raw_key})
    assert response.status_code == 200
    assert response.json()["limits"]["max_active_tasks"] == 1

    # 上限 3 个：再生成 2 个成功、第 4 个 400
    for _ in range(2):
        response = await client.post("/api/profile/api-keys", headers=auth_headers)
        assert response.status_code == 201
    response = await client.post("/api/profile/api-keys", headers=auth_headers)
    assert response.status_code == 400
    assert "上限" in response.json()["detail"]

    # 吊销后立即失效
    response = await client.delete(f"/api/profile/api-keys/{key_id}", headers=auth_headers)
    assert response.status_code == 200
    response = await client.get(f"{OPEN_PREFIX}/me", headers={"X-Api-Key": raw_key})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "key_revoked"

    # 重复吊销 → 400
    response = await client.delete(f"/api/profile/api-keys/{key_id}", headers=auth_headers)
    assert response.status_code == 400

    # 吊销后腾出名额，可再次生成
    response = await client.post("/api/profile/api-keys", headers=auth_headers)
    assert response.status_code == 201


async def test_cannot_revoke_other_users_key(client, auth_headers, interior_auth_headers):
    response = await client.post(
        "/api/profile/api-keys", json={"name": "mine"}, headers=auth_headers
    )
    assert response.status_code == 201
    other_key_id = response.json()["id"]

    # 另一个用户（内部账号）不可见、不可吊销 → 404 不泄露存在性
    response = await client.get("/api/profile/api-keys", headers=interior_auth_headers)
    assert response.status_code == 200
    assert all(item["id"] != other_key_id for item in response.json())
    response = await client.delete(
        f"/api/profile/api-keys/{other_key_id}", headers=interior_auth_headers
    )
    assert response.status_code == 404


async def test_api_keys_require_login(client):
    response = await client.get("/api/profile/api-keys")
    assert response.status_code in (401, 403)
