"""Reconstruct legacy balances into recharge/gift lots.

The command is deliberately dry-run by default. Run migration 031 first, take
a database backup, inspect the JSON reconciliation report, and only then pass
``--apply``. Existing loyalty-point balances are never changed.

Examples::

    python -m backend.scripts.reconstruct_sales_balances --report sales-reconcile.json
    python -m backend.scripts.reconstruct_sales_balances --apply --report sales-reconcile-applied.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from backend.models import (
    BillingOrder,
    ConsumptionAllocation,
    ConsumptionRecord,
    CreditLot,
    PointLedgerEntry,
    SalesPackage,
    User,
    UserWallet,
    async_session_factory,
)
from backend.services.sales import MONEY_QUANT, add_months, decimal_value, point_value
from backend.utils.time_utils import utc_now


@dataclass
class VirtualLot:
    id: str
    lot_type: str
    source_id: str
    points: Decimal
    remaining: Decimal
    unit_value: Decimal
    occurred_at: datetime


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _split_historical_points(
    historical_total: Decimal,
    package_recharge: Decimal,
    package_gift: Decimal,
) -> tuple[Decimal, Decimal]:
    """Split an actual historical credit using today's package ratio."""
    historical_total = point_value(historical_total)
    package_recharge = point_value(package_recharge)
    package_gift = point_value(package_gift)
    package_total = package_recharge + package_gift
    if package_total <= 0:
        return historical_total, Decimal("0.00")
    gift = point_value(historical_total * package_gift / package_total)
    # Put the two-decimal rounding remainder in recharge so totals are exact.
    return point_value(historical_total - gift), gift


async def reconstruct(*, apply: bool) -> dict[str, Any]:
    cutover = utc_now()
    safe_expiry = add_months(cutover, 12)
    report: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "cutover": cutover,
        "safe_expiry": safe_expiry,
        "users": [],
        "summary": {"processed": 0, "skipped": 0, "difference_points": Decimal("0")},
    }

    async with async_session_factory() as db:
        packages = {
            row.code: row
            for row in (await db.execute(select(SalesPackage))).scalars().all()
        }
        users = (
            await db.execute(
                select(User, UserWallet)
                .join(UserWallet, UserWallet.user_id == User.id)
                .order_by(User.id)
            )
        ).all()

        for user, wallet in users:
            marker = (
                await db.execute(
                    select(PointLedgerEntry.id).where(
                        PointLedgerEntry.event_type == "migration",
                        PointLedgerEntry.reference_type == "historical_reconstruction",
                        PointLedgerEntry.reference_id == user.id,
                    )
                )
            ).scalar_one_or_none()
            existing_lot = (
                await db.execute(select(CreditLot.id).where(CreditLot.user_id == user.id).limit(1))
            ).scalar_one_or_none()
            if marker or existing_lot:
                report["users"].append(
                    {
                        "user_id": user.id,
                        "username": user.username,
                        "status": "skipped",
                        "reason": "already reconstructed or point lots already exist",
                    }
                )
                report["summary"]["skipped"] += 1
                continue

            orders = list(
                (
                    await db.execute(
                        select(BillingOrder)
                        .where(BillingOrder.user_id == user.id, BillingOrder.status == "completed")
                        .order_by(BillingOrder.paid_at, BillingOrder.created_at, BillingOrder.id)
                    )
                ).scalars().all()
            )
            consumptions = list(
                (
                    await db.execute(
                        select(ConsumptionRecord)
                        .where(ConsumptionRecord.user_id == user.id)
                        .order_by(ConsumptionRecord.created_at, ConsumptionRecord.id)
                    )
                ).scalars().all()
            )

            events: list[tuple[datetime, int, str, Any]] = []
            order_specs: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
            unknown_package_codes: set[str] = set()
            for order in orders:
                package = packages.get(order.product_code)
                if package is not None:
                    # Historical package totals may differ from today's package
                    # definition. Split the points that were actually credited
                    # using the current recharge/gift ratio; do not invent points.
                    historical_total = point_value(order.package_balance_wen)
                    recharge, gift = _split_historical_points(
                        historical_total,
                        decimal_value(package.recharge_points),
                        decimal_value(package.gift_points),
                    )
                else:
                    # Unknown historical products cannot be ratio-split safely.
                    # Keep their points as recharge and surface them in the report.
                    unknown_package_codes.add(order.product_code)
                    recharge = point_value(order.package_balance_wen)
                    gift = Decimal("0.00")
                total = recharge + gift
                unit = (
                    (Decimal(order.actual_payment_cents) / Decimal("100") / total).quantize(
                        Decimal("0.00000001"), rounding=ROUND_HALF_UP
                    )
                    if total > 0
                    else Decimal("0")
                )
                order_specs[order.id] = (recharge, gift, unit)
                events.append((order.paid_at or order.created_at, 0, "order", order))
            for record in consumptions:
                events.append((record.created_at, 1, "consumption", record))
            events.sort(key=lambda item: (item[0], item[1], item[3].id))

            lots: list[VirtualLot] = []
            allocation_specs: list[tuple[ConsumptionRecord, VirtualLot | None, str, Decimal, Decimal]] = []
            recharge_balance = Decimal("0.00")
            gift_balance = Decimal("0.00")
            total_income = Decimal("0")

            for occurred_at, _priority, kind, entity in events:
                if kind == "order":
                    recharge, gift, unit = order_specs[entity.id]
                    for lot_type, points in (("recharge", recharge), ("gift", gift)):
                        if points <= 0:
                            continue
                        lot = VirtualLot(
                            id=str(uuid4()),
                            lot_type=lot_type,
                            source_id=entity.id,
                            points=points,
                            remaining=points,
                            unit_value=unit,
                            occurred_at=occurred_at,
                        )
                        lots.append(lot)
                        if lot_type == "gift":
                            gift_balance += points
                        else:
                            recharge_balance += points
                    continue

                record = entity
                sale_points = point_value(record.sales_points or record.consumed_wen)
                remaining = sale_points
                before_recharge, before_gift = recharge_balance, gift_balance
                record_income = Decimal("0")
                gift_used = Decimal("0")

                for lot_type in ("gift", "recharge"):
                    for lot in sorted(
                        (item for item in lots if item.lot_type == lot_type and item.remaining > 0),
                        key=lambda item: (item.occurred_at, item.id),
                    ):
                        if remaining <= 0:
                            break
                        take = min(remaining, lot.remaining)
                        lot.remaining = point_value(lot.remaining - take)
                        remaining = point_value(remaining - take)
                        if lot_type == "gift":
                            gift_balance = point_value(gift_balance - take)
                            gift_used += take
                        else:
                            recharge_balance = point_value(recharge_balance - take)
                        income = _money(take * lot.unit_value)
                        record_income += income
                        allocation_specs.append((record, lot, lot_type, take, income))
                    if remaining <= 0:
                        break

                if remaining > 0:
                    recharge_balance = point_value(recharge_balance - remaining)
                    allocation_specs.append((record, None, "recharge", remaining, Decimal("0")))

                cost = decimal_value(record.cost_cny)
                cost_points = _money(cost * Decimal("10"))
                multiplier = (
                    (sale_points / cost_points).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                    if cost_points > 0
                    else Decimal("4")
                )
                profit = _money(record_income - cost)
                if apply:
                    record.cost_points = cost_points
                    record.sales_multiplier = multiplier
                    record.sales_points = sale_points
                    record.gift_points_used = point_value(gift_used)
                    record.recharge_points_used = point_value(sale_points - gift_used)
                    record.recharge_balance_before = point_value(before_recharge)
                    record.gift_balance_before = point_value(before_gift)
                    record.recharge_balance_after = point_value(recharge_balance)
                    record.gift_balance_after = point_value(gift_balance)
                    record.weighted_unit_value_yuan = (
                        (record_income / sale_points).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
                        if sale_points > 0
                        else Decimal("0")
                    )
                    record.folded_income_yuan = _money(record_income)
                    record.profit_yuan = profit
                    record.profit_margin = (
                        (profit / record_income).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
                        if record_income > 0
                        else None
                    )
                total_income += record_income

            calculated = point_value(recharge_balance + gift_balance)
            legacy = point_value(wallet.balance_wen)
            difference = point_value(legacy - calculated)
            # Unknown opening balances, manual corrections, and old data gaps are
            # intentionally absorbed by recharge balance; gift never goes negative.
            recharge_balance = point_value(recharge_balance + difference)

            # A negative difference represents historical deductions not visible
            # in the reconstructed event stream. Consume recharge lots FIFO so
            # their remaining total stays aligned with the wallet as far as
            # possible; any excess remains valid negative recharge debt.
            if difference < 0:
                adjustment_remaining = -difference
                for lot in sorted(
                    (item for item in lots if item.lot_type == "recharge" and item.remaining > 0),
                    key=lambda item: (item.occurred_at, item.id),
                ):
                    if adjustment_remaining <= 0:
                        break
                    take = min(adjustment_remaining, lot.remaining)
                    lot.remaining = point_value(lot.remaining - take)
                    adjustment_remaining = point_value(adjustment_remaining - take)

            if apply:
                for lot in lots:
                    db.add(
                        CreditLot(
                            id=lot.id,
                            user_id=user.id,
                            external_user_id=user.external_user_id,
                            lot_type=lot.lot_type,
                            source_type="billing_order",
                            source_id=lot.source_id,
                            initial_points=lot.points,
                            remaining_points=lot.remaining,
                            unit_value_yuan=lot.unit_value,
                            valid_from=cutover,
                            expires_at=safe_expiry,
                            status="active" if lot.remaining > 0 else "exhausted",
                            created_at=lot.occurred_at,
                            updated_at=cutover,
                        )
                    )
                await db.flush()

                allocation_cost_by_record: dict[str, Decimal] = {}
                allocation_indexes: dict[str, int] = {}
                allocation_counts: dict[str, int] = {}
                for record, *_rest in allocation_specs:
                    allocation_counts[record.id] = allocation_counts.get(record.id, 0) + 1

                for record, lot, lot_type, points, income in allocation_specs:
                    cost = decimal_value(record.cost_cny)
                    sale_points = point_value(record.sales_points or record.consumed_wen)
                    allocation_indexes[record.id] = allocation_indexes.get(record.id, 0) + 1
                    if allocation_indexes[record.id] == allocation_counts[record.id]:
                        allocated_cost = _money(cost) - allocation_cost_by_record.get(record.id, Decimal("0"))
                    else:
                        allocated_cost = _money(cost * points / sale_points) if sale_points > 0 else Decimal("0")
                        allocation_cost_by_record[record.id] = (
                            allocation_cost_by_record.get(record.id, Decimal("0")) + allocated_cost
                        )
                    db.add(
                        ConsumptionAllocation(
                            consumption_id=record.id,
                            lot_id=lot.id if lot else None,
                            lot_type=lot_type,
                            points=points,
                            unit_value_yuan=lot.unit_value if lot else Decimal("0"),
                            folded_income_yuan=income,
                            allocated_cost_yuan=allocated_cost,
                            created_at=record.created_at,
                            updated_at=cutover,
                        )
                    )

                if difference > 0:
                    adjustment_lot = CreditLot(
                        user_id=user.id,
                        external_user_id=user.external_user_id,
                        lot_type="recharge",
                        source_type="historical_adjustment",
                        source_id=user.id,
                        initial_points=difference,
                        remaining_points=difference,
                        unit_value_yuan=Decimal("0"),
                        valid_from=cutover,
                        expires_at=safe_expiry,
                        status="active",
                    )
                    db.add(adjustment_lot)
                    await db.flush()

                wallet.recharge_balance_points = recharge_balance
                wallet.gift_balance_points = point_value(gift_balance)
                # Preserve wallet.points exactly as requested.
                db.add(
                    PointLedgerEntry(
                        user_id=user.id,
                        event_type="migration",
                        recharge_delta=recharge_balance,
                        gift_delta=gift_balance,
                        loyalty_delta=0,
                        recharge_after=recharge_balance,
                        gift_after=gift_balance,
                        loyalty_after=wallet.points,
                        lot_id=None,
                        reference_type="historical_reconstruction",
                        reference_id=user.id,
                        description="历史余额按当前套餐比例重建；差额归入充值点数",
                    )
                )

                for order in orders:
                    recharge, gift, unit = order_specs[order.id]
                    order.recharge_points = recharge
                    order.gift_points = gift
                    order.total_points = point_value(recharge + gift)
                    order.validity_months = 12
                    order.unit_value_yuan = unit

            report["users"].append(
                {
                    "user_id": user.id,
                    "external_user_id": user.external_user_id,
                    "username": user.username,
                    "status": "applied" if apply else "planned",
                    "legacy_balance": legacy,
                    "calculated_before_adjustment": calculated,
                    "recharge_balance": recharge_balance,
                    "gift_balance": gift_balance,
                    "difference_to_recharge": difference,
                    "orders": len(orders),
                    "consumptions": len(consumptions),
                    "unknown_package_codes": sorted(unknown_package_codes),
                    "reconstructed_income_yuan": _money(total_income),
                    "loyalty_points_preserved": wallet.points,
                }
            )
            report["summary"]["processed"] += 1
            report["summary"]["difference_points"] += difference

        if apply:
            await db.commit()
        else:
            await db.rollback()
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist the reconstruction")
    parser.add_argument("--report", type=Path, help="write the JSON reconciliation report")
    args = parser.parse_args()
    report = await reconstruct(apply=args.apply)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=_json_value)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    asyncio.run(main())
