from decimal import Decimal

from backend.services.billing import cost_to_wen, list_packages


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
