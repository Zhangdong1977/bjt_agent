from decimal import Decimal

from backend.api.billing import router as billing_router
from backend.config import Settings
from backend.schemas.billing import PackageResponse, PaymentQrResponse
from backend.services.billing import _is_package_visible, cost_to_wen, list_packages


def test_cost_to_wen_uses_markup_and_rounds_up():
    assert cost_to_wen(Decimal("0")) == 0
    assert cost_to_wen(Decimal("1")) == 40
    assert cost_to_wen(Decimal("1.001")) == 41
    assert cost_to_wen(Decimal("0.025")) == 1


def test_recharge_packages_match_product_spec():
    packages = {item.code: item for item in list_packages()}

    assert packages["experience"].amount_cents == 3000
    assert packages["experience"].balance_wen == 350
    assert packages["basic"].amount_cents == 10000
    assert packages["basic"].balance_wen == 1200
    assert packages["premium"].amount_cents == 30000
    assert packages["premium"].balance_wen == 4000
    assert packages["luxury"].amount_cents == 100000
    assert packages["luxury"].balance_wen == 15000


def test_simulated_payment_route_and_contract_are_removed():
    assert all("mock-pay" not in route.path for route in billing_router.routes)
    assert "payment_mode" not in PackageResponse.model_fields
    assert "payment_mode" not in PaymentQrResponse.model_fields
    assert "billing_real_pay_enabled" not in Settings.model_fields
    assert "billing_real_package_codes" not in Settings.model_fields


def _settings(**overrides) -> Settings:
    """Build a Settings instance without touching the cached .env-driven one.

    Pass _env_file=None to pydantic-settings so the on-disk dev .env (which has
    BILLING_TEST_PACKAGE_ENABLED=true) doesn't leak into these isolated tests.
    """
    base = {
        "secret_key": "test",
        "database_url": "postgresql+asyncpg://u:p@h:5432/d",
        "redis_url": "redis://h:6379/0",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_test_package_hidden_by_default_fail_closed():
    """prod 漏配环境变量时，1 分钱测试套餐必须 fail-closed 隐藏。"""
    settings = _settings()  # billing_test_package_enabled 默认 False，hidden 清单为空
    assert _is_package_visible("test", settings) is False
    # 其余正式套餐不受影响
    for code in ("experience", "basic", "premium", "luxury"):
        assert _is_package_visible(code, settings) is True


def test_test_package_visible_only_when_explicitly_enabled():
    settings = _settings(billing_test_package_enabled=True)
    assert _is_package_visible("test", settings) is True


def test_hidden_package_codes_still_overrides_anything():
    """billing_hidden_package_codes 仍可下架任意套餐（含正式套餐）。"""
    settings = _settings(billing_test_package_enabled=True, billing_hidden_package_codes="basic,premium")
    assert _is_package_visible("test", settings) is True
    assert _is_package_visible("basic", settings) is False
    assert _is_package_visible("premium", settings) is False
    assert _is_package_visible("luxury", settings) is True


def test_list_packages_never_returns_test_in_prod_default():
    """模拟 prod：未设任何 billing_* 环境变量时，packages 接口不含 test。"""
    settings = _settings()
    # 直接走 _is_package_visible 反映 list_packages 的过滤结果
    codes = {code for code in ("test", "experience", "basic", "premium", "luxury")
             if _is_package_visible(code, settings)}
    assert "test" not in codes
    assert codes == {"experience", "basic", "premium", "luxury"}
