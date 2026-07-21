"""充值安全 + 充值链路兜底相关测试。

fb51261（2026-07-17）已删除 mock-pay 端点 + payment_mode 字段，本测试覆盖
fb51261 之后的增量安全加固与本次故障（BJT202607210252559C0165 真实付款未入账）修复：

1. mock-pay 端点确实已从路由表移除（固化 fb51261 的修复，防回归）
2. 隐藏套餐（如 test）无法 preview / create order（fb51261 之后的额外加固）
3. complete_order 的 allow_expired_if_paid 行为（本次故障根除）
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.api import billing as billing_api
from backend.config import Settings
from backend.services import billing as billing_svc


# -----------------------------
# 工具：构造无 .env 的 Settings（避免 dev .env 干扰）
# -----------------------------

def _settings(**overrides) -> Settings:
    """Build a Settings instance without touching the cached .env-driven one.

    Pass _env_file=None so the on-disk dev .env (which has
    BILLING_TEST_PACKAGE_ENABLED=true) doesn't leak into these isolated tests.
    """
    base = {
        "secret_key": "test",
        "database_url": "postgresql+asyncpg://u:p@h:5432/d",
        "redis_url": "redis://h:6379/0",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


# -----------------------------
# 1. mock-pay 端点必须不存在（固化 fb51261 的修复）
# -----------------------------

def test_mock_pay_endpoint_removed():
    """mock-pay 后门必须从路由表彻底移除（fb51261 已删，本测试防回归）。

    历史背景：该端点曾被外部用户利用，对任意套餐订单（含豪华 15000 文）直接
    调用 complete_order 凭空加文，造成 ~1.5 万元损失（见运维事故记录）。
    """
    paths = {route.path for route in billing_api.router.routes}
    mockpay_routes = [p for p in paths if "mock-pay" in p]
    assert mockpay_routes == [], f"发现残留 mock-pay 路由: {mockpay_routes}"

    # 同时确认没有任何 mock 相关路由
    for route in billing_api.router.routes:
        if "mock" in getattr(route, "path", "").lower():
            pytest.fail(f"发现 mock 相关路由: {route.path}")


# -----------------------------
# 2. 隐藏套餐无法 preview / create order
# -----------------------------

def test_hidden_package_rejected_in_visibility_check():
    """prod 默认配置下，test 套餐必须不可见。"""
    settings = _settings()  # billing_test_package_enabled=False
    assert billing_svc._is_package_visible("test", settings) is False


def test_normal_packages_remain_visible_in_prod():
    """正式套餐在 prod 默认配置下必须可见（不能因为安全加固误伤）。"""
    settings = _settings()
    for code in ("experience", "basic", "premium", "luxury"):
        assert billing_svc._is_package_visible(code, settings) is True


@pytest.mark.asyncio
async def test_preview_order_blocks_hidden_package():
    """preview_order 必须对隐藏套餐（如 test）抛 400，即使调用者知道 code。

    堵"前端不展示但后端不校验"的攻击面：fb51261 删了 mock-pay，但 create_order
    仍未做可见性校验——本测试固化本次新增的 _is_package_visible 校验。
    """
    from fastapi import HTTPException

    settings = _settings()  # prod 默认配置：test 套餐不可见
    user = SimpleNamespace(id="u1", username="user1")

    with patch.object(billing_svc, "get_settings", return_value=settings):
        with pytest.raises(HTTPException) as exc:
            await billing_svc.preview_order(
                db=MagicMock(),
                current_user=user,
                package_code="test",
                coupon_id=None,
                use_points=0,
            )
        assert exc.value.status_code == 400
        assert "套餐不存在" in exc.value.detail


# -----------------------------
# 3. complete_order 的 allow_expired_if_paid 参数（本次故障根除）
# -----------------------------

@pytest.mark.asyncio
async def test_complete_order_rejects_expired_by_default():
    """默认（allow_expired_if_paid=False）：过期订单拒绝，置 cancelled。

    保持 API 端点（get_order_status）的原有行为不变——过期订单不再可支付。
    """
    from fastapi import HTTPException

    settings = _settings()
    user = SimpleNamespace(id="u1")
    expired_order = SimpleNamespace(
        user_id="u1",
        status="pending",
        expires_at=billing_svc.utc_now() - timedelta(minutes=5),
    )

    db = MagicMock()
    db.flush = AsyncMock()

    with patch.object(billing_svc, "get_settings", return_value=settings):
        with pytest.raises(HTTPException) as exc:
            await billing_svc.complete_order(db, user, expired_order)
        assert exc.value.status_code == 400
        assert "已过期" in exc.value.detail
    # 订单被置 cancelled + flush
    assert expired_order.status == "cancelled"
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_complete_order_allows_expired_when_paid():
    """allow_expired_if_paid=True：即使过期也能入账（定时任务用，杜绝吞钱）。

    场景：交行真实付款回调晚于订单 30 分钟过期——钱已收就必须给文。
    本次故障订单 BJT202607210252559C0165 即为此场景（已手工补单，本测试固化逻辑）。
    """
    settings = _settings()
    user = SimpleNamespace(id="u1")
    wallet = SimpleNamespace(points=0, balance_wen=0)

    expired_order = SimpleNamespace(
        id="order-1",
        user_id="u1",
        status="pending",
        expires_at=billing_svc.utc_now() - timedelta(minutes=5),
        points_used=0,
        package_balance_wen=1200,
        product_name="基础套餐",
        coupon_id=None,
        paid_at=None,
        balance_after_wen=None,
    )

    db = MagicMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    with patch.object(billing_svc, "get_settings", return_value=settings):
        result = await billing_svc.complete_order(
            db, user, expired_order, wallet=wallet, allow_expired_if_paid=True
        )

    # 入账成功
    assert result.status == "completed"
    assert wallet.balance_wen == 1200  # 加了 1200 文
    assert result.paid_at is not None
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_complete_order_idempotent_on_completed():
    """已 completed 的订单再次调用直接返回，不重复入账（定时任务幂等保证）。

    定时任务每 60s 扫一次，可能多次扫到同一条订单；complete_order 必须幂等。
    """
    user = SimpleNamespace(id="u1")
    completed_order = SimpleNamespace(
        user_id="u1",
        status="completed",
    )

    db = MagicMock()
    result = await billing_svc.complete_order(db, user, completed_order)
    assert result is completed_order
    # 不应触发任何写动作
    db.flush.assert_not_called()
