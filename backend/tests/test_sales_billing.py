"""Unit tests for split recharge/gift point accounting."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.models import ConsumptionAllocation, PointLedgerEntry
from backend.api.admin_sales import consumption_details, list_grants
from backend.api.billing import _order_points_status
from backend.scripts.reconstruct_sales_balances import _split_historical_points
from backend.services.billing import cost_to_points, sales_points_for
from backend.services.sales import allocate_consumption, expire_user_lots
from backend.utils.time_utils import utc_now


def _rows(items):
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _lot(lot_id, lot_type, points, unit_value, *, expires_in_days=30):
    return SimpleNamespace(
        id=lot_id,
        lot_type=lot_type,
        remaining_points=Decimal(points),
        unit_value_yuan=Decimal(unit_value),
        status="active",
        expires_at=utc_now() + timedelta(days=expires_in_days),
    )


def test_cost_and_sales_points_round_half_up_to_two_decimals():
    assert cost_to_points(Decimal("1.001")) == Decimal("10.010000")
    assert sales_points_for(Decimal("1.001"), Decimal("4")) == Decimal("40.04")
    assert sales_points_for(Decimal("0.00125"), Decimal("4")) == Decimal("0.05")


def test_historical_package_split_preserves_actual_total_at_current_ratio():
    recharge, gift = _split_historical_points(
        Decimal("1000"), Decimal("1000"), Decimal("200")
    )
    assert recharge == Decimal("833.33")
    assert gift == Decimal("166.67")
    assert recharge + gift == Decimal("1000.00")


def test_order_point_status_distinguishes_usable_expired_and_exhausted_lots():
    completed = SimpleNamespace(status="completed")
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)

    assert _order_points_status(
        completed,
        remaining_points=10,
        raw_remaining_points=10,
        points_expires_at=future,
    ) == "active"
    assert _order_points_status(
        completed,
        remaining_points=0,
        raw_remaining_points=10,
        points_expires_at=past,
    ) == "expired"
    assert _order_points_status(
        completed,
        remaining_points=0,
        raw_remaining_points=0,
        points_expires_at=future,
    ) == "exhausted"
    assert _order_points_status(
        SimpleNamespace(status="pending"),
        remaining_points=0,
        raw_remaining_points=0,
        points_expires_at=None,
    ) == "not_credited"


@pytest.mark.asyncio
async def test_allocate_gift_first_fifo_then_recharge_debt_and_weighted_income():
    gift_early = _lot("gift-early", "gift", "10", "0.30", expires_in_days=7)
    gift_late = _lot("gift-late", "gift", "20", "0.20", expires_in_days=30)
    recharge = _lot("recharge", "recharge", "40", "0.10")
    wallet = SimpleNamespace(
        user_id="user-1",
        gift_balance_points=Decimal("30"),
        recharge_balance_points=Decimal("40"),
        balance_wen=70,
        points=5,
    )
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _rows([]),
            _rows([gift_early, gift_late]),
            _rows([recharge]),
        ]
    )
    db.add = MagicMock()

    result = await allocate_consumption(
        db,
        wallet,
        consumption_id="consumption-1",
        sales_points=Decimal("80"),
        cost_yuan=Decimal("1"),
        task_id="task-1",
    )

    assert gift_early.status == "exhausted"
    assert gift_late.status == "exhausted"
    assert recharge.status == "exhausted"
    assert gift_early.remaining_points == Decimal("0.00")
    assert gift_late.remaining_points == Decimal("0.00")
    assert wallet.gift_balance_points == Decimal("0.00")
    assert wallet.recharge_balance_points == Decimal("-10.00")
    assert wallet.balance_wen == -10
    assert result["gift_used"] == Decimal("30.00")
    assert result["recharge_used"] == Decimal("50.00")
    # Gift consumption mints no loyalty; only 50 recharge points do.
    assert result["earned_loyalty"] == 50
    assert wallet.points == 55
    # Income is the weighted sum of actual lot values; debt has zero value.
    assert result["folded_income"] == Decimal("11.000000")
    assert result["weighted_unit_value"] == Decimal("0.13750000")

    allocations = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], ConsumptionAllocation)
    ]
    assert [item.lot_id for item in allocations] == [
        "gift-early",
        "gift-late",
        "recharge",
        None,
    ]
    assert sum((item.allocated_cost_yuan for item in allocations), Decimal("0")) == Decimal("1.000000")

    ledgers = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], PointLedgerEntry)
    ]
    assert [item.gift_after for item in ledgers] == [
        Decimal("20.00"),
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    ]
    assert [item.recharge_after for item in ledgers] == [
        Decimal("40.00"),
        Decimal("40.00"),
        Decimal("0.00"),
        Decimal("-10.00"),
    ]
    assert [item.loyalty_delta for item in ledgers] == [0, 0, 0, 50]

    fifo_query = str(db.execute.await_args_list[1].args[0])
    assert "ORDER BY credit_lots.expires_at" in fifo_query


@pytest.mark.asyncio
async def test_expired_lot_is_removed_without_making_gift_balance_negative():
    expired = _lot("gift-expired", "gift", "4.25", "0", expires_in_days=-1)
    wallet = SimpleNamespace(
        user_id="user-2",
        gift_balance_points=Decimal("4.25"),
        recharge_balance_points=Decimal("2"),
        balance_wen=6,
        points=0,
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=_rows([expired]))
    db.add = MagicMock()

    count = await expire_user_lots(db, wallet)

    assert count == 1
    assert expired.status == "expired"
    assert wallet.gift_balance_points == Decimal("0.00")
    assert wallet.recharge_balance_points == Decimal("2")
    assert wallet.balance_wen == 2
    ledger = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], PointLedgerEntry)
    )
    assert ledger.event_type == "expire"
    assert ledger.gift_delta == Decimal("-4.25")


@pytest.mark.asyncio
async def test_grant_list_reads_expanded_subquery_columns_by_position():
    batch = SimpleNamespace(
        id="batch-1",
        name="推广活动",
        created_at=utc_now(),
        account_count=2,
        points_per_account=Decimal("100"),
        total_points=Decimal("200"),
        validity_value=1,
        validity_unit="month",
        reason="推广",
        remark=None,
    )
    expires_at = utc_now() + timedelta(days=30)
    count_result = MagicMock()
    count_result.scalar_one.return_value = 1
    rows_result = MagicMock()
    rows_result.all.return_value = [
        (
            batch,
            "batch-1",
            1,
            0,
            2,
            ["13900000002", "13900000001"],
            expires_at,
            Decimal("1.25"),
        )
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[count_result, rows_result])

    response = await list_grants(
        db=db,
        name=None,
        start_time=None,
        end_time=None,
        limit=20,
        offset=0,
    )

    assert response["total"] == 1
    assert response["items"][0]["termination_status"] == "部分终止"
    assert response["items"][0]["account_usernames"] == [
        "13900000001",
        "13900000002",
    ]
    assert response["items"][0]["generated_cost_yuan"] == 1.25


@pytest.mark.asyncio
async def test_consumption_details_exposes_reliable_task_identity_and_status():
    record = SimpleNamespace(
        id="consumption-1",
        created_at=utc_now(),
        task_id="task-failed",
        task_type="duplicate",
        task_status="failed",
        project_name="QA查重",
        recharge_balance_before=Decimal("100"),
        gift_balance_before=Decimal("30"),
        cost_points=Decimal("15"),
        sales_multiplier=Decimal("4"),
        sales_points=Decimal("60"),
        gift_points_used=Decimal("30"),
        recharge_points_used=Decimal("30"),
        recharge_balance_after=Decimal("70"),
        gift_balance_after=Decimal("0"),
        weighted_unit_value_yuan=Decimal("0.05"),
        folded_income_yuan=Decimal("3"),
        cost_cny=Decimal("1.5"),
        profit_yuan=Decimal("1.5"),
        profit_margin=Decimal("0.5"),
    )
    user = SimpleNamespace(
        external_user_id=6671,
        username="19900000102",
        nickname="QA用户",
    )
    count_result = MagicMock()
    count_result.scalar_one.return_value = 1
    rows_result = MagicMock()
    rows_result.all.return_value = [(record, user)]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[count_result, rows_result])

    response = await consumption_details(db=db, limit=20, offset=0)

    item = response["items"][0]
    assert item["task_id"] == "task-failed"
    assert item["task_type"] == "duplicate"
    assert item["task_status"] == "failed"
