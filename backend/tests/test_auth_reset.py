"""Tests for the password-reset endpoints (send-reset-sms / reset-password).

These endpoints forward to the operate-two (运营平台) bridge endpoints
``/aiGetResetCode`` and ``/aiResetPwd``. Tests mock ``httpx.AsyncClient`` to
verify the forwarding payload / headers / error passthrough, and bypass the
image-captcha gate (already covered by captcha_service tests) via a
``verify_captcha`` monkeypatch.

Rate-limit isolation: each request carries a unique ``X-Forwarded-For`` so the
slowapi in-memory/redis counters (keyed by ``get_client_ip``) don't bleed
across cases (send-reset-sms is 1/minute).
"""

import httpx
import pytest
from httpx import AsyncClient

from backend.api import auth as auth_module


def _make_fake_client(payload, *, raise_connect_error=False):
    """Build a fake ``httpx.AsyncClient`` replacement.

    Records every ``post(url, json, headers)`` call onto ``FakeClient.calls``
    (shared across instances) and returns ``payload`` as the JSON response.
    When ``raise_connect_error`` is set, ``post`` raises ``httpx.ConnectError``
    (a subclass of ``httpx.RequestError``) to exercise the 503 path.
    """

    calls = []

    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def json(self):
            return self._data

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append({"url": url, "json": json, "headers": headers})
            if raise_connect_error:
                raise httpx.ConnectError("upstream unreachable")
            return FakeResponse(payload)

    FakeClient.calls = calls
    return FakeClient


@pytest.fixture(autouse=True)
def _disable_rate_limit(monkeypatch):
    """禁用 slowapi 限流：转发逻辑测试不应被 in-memory/redis 计数干扰。

    限流本身由 test_rate_limit 覆盖；此处关掉后每个用例可自由调用被测端点，
    无需依赖 X-Forwarded-For 做 IP 隔离。
    """
    monkeypatch.setattr(auth_module.limiter, "enabled", False)


class TestSendResetSms:
    """POST /api/auth/send-reset-sms → forwards to /aiGetResetCode."""

    @pytest.fixture(autouse=True)
    def _bypass_captcha(self, monkeypatch):
        # 图形验证码由 captcha_service 测试覆盖；此处默认放行，聚焦转发逻辑
        monkeypatch.setattr(auth_module, "verify_captcha", lambda *a, **k: True)

    @pytest.mark.asyncio
    async def test_forwards_to_aiGetResetCode(self, client: AsyncClient, monkeypatch):
        fake = _make_fake_client({"code": 200, "msg": "验证码已发送"})
        monkeypatch.setattr(httpx, "AsyncClient", fake)

        resp = await client.post(
            "/api/auth/send-reset-sms",
            json={"phone": "13800138000", "captcha_id": "cid", "captcha_code": "1234"},
            headers={"X-Forwarded-For": "10.0.0.11"},
        )

        assert resp.status_code == 200
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["url"].endswith("/aiGetResetCode")
        assert call["json"] == {"account": "13800138000"}
        assert "X-Internal-Token" in call["headers"]

    @pytest.mark.asyncio
    async def test_captcha_invalid_returns_400(self, client: AsyncClient, monkeypatch):
        # 覆盖 autouse 默认：图形验证码错误应在转发前拦截
        monkeypatch.setattr(auth_module, "verify_captcha", lambda *a, **k: False)
        fake = _make_fake_client({"code": 200, "msg": "ok"})
        monkeypatch.setattr(httpx, "AsyncClient", fake)

        resp = await client.post(
            "/api/auth/send-reset-sms",
            json={"phone": "13800138000", "captcha_id": "cid", "captcha_code": "0000"},
            headers={"X-Forwarded-For": "10.0.0.12"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "图形验证码错误或已失效"
        assert fake.calls == []  # 未转发到运营平台

    @pytest.mark.asyncio
    async def test_upstream_error_passthrough(self, client: AsyncClient, monkeypatch):
        fake = _make_fake_client({"code": 500, "msg": "该手机号未注册，请先注册"})
        monkeypatch.setattr(httpx, "AsyncClient", fake)

        resp = await client.post(
            "/api/auth/send-reset-sms",
            json={"phone": "13800138000", "captcha_id": "cid", "captcha_code": "1234"},
            headers={"X-Forwarded-For": "10.0.0.13"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "该手机号未注册，请先注册"

    @pytest.mark.asyncio
    async def test_upstream_request_error_returns_503(self, client: AsyncClient, monkeypatch):
        fake = _make_fake_client({}, raise_connect_error=True)
        monkeypatch.setattr(httpx, "AsyncClient", fake)

        resp = await client.post(
            "/api/auth/send-reset-sms",
            json={"phone": "13800138000", "captcha_id": "cid", "captcha_code": "1234"},
            headers={"X-Forwarded-For": "10.0.0.14"},
        )

        assert resp.status_code == 503
        assert resp.json()["detail"] == "短信服务不可用，请稍后重试"


class TestResetPassword:
    """POST /api/auth/reset-password → forwards to /aiResetPwd."""

    @pytest.mark.asyncio
    async def test_forwards_to_aiResetPwd(self, client: AsyncClient, monkeypatch):
        fake = _make_fake_client({"code": 200})
        monkeypatch.setattr(httpx, "AsyncClient", fake)

        resp = await client.post(
            "/api/auth/reset-password",
            json={
                "phone": "13800138000",
                "sms_code": "888888",
                "new_password": "Abc123!@",
                "confirm_new_password": "Abc123!@",
            },
            headers={"X-Forwarded-For": "10.0.0.21"},
        )

        assert resp.status_code == 200
        assert resp.json()["message"] == "重置成功，请登录"
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["url"].endswith("/aiResetPwd")
        assert call["json"] == {
            "account": "13800138000",
            "verificationCode": "888888",
            "password": "Abc123!@",
            "confirmPassword": "Abc123!@",
        }

    @pytest.mark.asyncio
    async def test_upstream_error_passthrough(self, client: AsyncClient, monkeypatch):
        fake = _make_fake_client({"code": 500, "msg": "短信验证码有误"})
        monkeypatch.setattr(httpx, "AsyncClient", fake)

        resp = await client.post(
            "/api/auth/reset-password",
            json={
                "phone": "13800138000",
                "sms_code": "000000",
                "new_password": "Abc123!@",
                "confirm_new_password": "Abc123!@",
            },
            headers={"X-Forwarded-For": "10.0.0.22"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "短信验证码有误"

    @pytest.mark.asyncio
    async def test_password_mismatch_rejected_by_schema(self, client: AsyncClient):
        # Pydantic model_validator 在转发前拦截两次密码不一致
        resp = await client.post(
            "/api/auth/reset-password",
            json={
                "phone": "13800138000",
                "sms_code": "888888",
                "new_password": "Abc123!@",
                "confirm_new_password": "Different1!",
            },
            headers={"X-Forwarded-For": "10.0.0.23"},
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_phone_rejected_by_schema(self, client: AsyncClient):
        resp = await client.post(
            "/api/auth/reset-password",
            json={
                "phone": "12345",
                "sms_code": "888888",
                "new_password": "Abc123!@",
                "confirm_new_password": "Abc123!@",
            },
            headers={"X-Forwarded-For": "10.0.0.24"},
        )

        assert resp.status_code == 422
