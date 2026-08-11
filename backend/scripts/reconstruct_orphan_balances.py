"""Backfill a reconciliation credit lot for orphan wallet balances.

Scenario this script fixes
--------------------------
Migration ``031`` added the ``recharge_balance_points`` / ``gift_balance_points``
wallet columns but the companion ``reconstruct_sales_balances.py`` was never run
in production.  ``ensure_wallet`` lazily copies ``balance_wen`` into
``recharge_balance_points`` so users keep passing the balance precheck, yet no
``credit_lots`` row is ever created.  Settlement then falls through to the
``allocate_consumption`` negative-balance debt branch (``sales.py``), the wallet
number is decremented in place, and the per-account "当前扣费订单" card
(which aggregates ``credit_lots``) stays empty forever.

This script does NOT try to replay the historical order+consumption event
stream (the official reconstructor does that, but its reading of
``billing_orders.package_balance_wen`` double-counts production legacy data).
Instead it trusts the current wallet numbers as the source of truth and emits a
single reconciliation lot per lot_type so that:

  * settlement finds a real lot instead of the debt branch;
  * the "当前扣费订单" card shows the order (the lot is attached to the user's
    most recent completed billing order via ``source_type='billing_order'``).

Safety properties
-----------------
* Wallet fields (``balance_wen``, ``recharge_balance_points``,
  ``gift_balance_points``, ``points``) are never written.
* Historical ``consumption_records`` / ``billing_orders`` rows are never touched.
* Idempotent: a ``PointLedgerEntry`` migration marker + the existing-lot check
  guarantee each user is processed at most once.
* Only users that have a wallet AND zero credit lots AND non-zero balance AND at
  least one completed order are eligible; everyone else is skipped.
* Dry-run by default; ``--apply`` commits in a single transaction.

Examples::

    python -m backend.scripts.reconstruct_orphan_balances --report orphan-dry.json
    python -m backend.scripts.reconstruct_orphan_balances --apply --report orphan-applied.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, nullslast, select

from backend.models import (
    BillingOrder,
    CreditLot,
    PointLedgerEntry,
    User,
    UserWallet,
    async_session_factory,
)
from backend.services.sales import (
    POINT_QUANT,
    add_months,
    decimal_value,
    point_value,
)
from backend.utils.time_utils import utc_now

# Reuse the same marker semantics as reconstruct_sales_balances.py so that both
# scripts agree a wallet has been reconciled and neither reprocesses the other's
# work.  reference_id is the user id.
MARKER_EVENT_TYPE = "migration"
MARKER_REFERENCE_TYPE = "historical_reconstruction"
MARKER_DESCRIPTION = "孤儿钱包余额补建对账批次（以当前余额为基准，未重放历史）"

SAFE_VALIDITY_MONTHS = 12


@dataclass
class BackfillPlan:
    user_id: str
    username: str
    order_id: str
    order_no: str
    product_name: str
    paid_at: Any
    recharge_points: Decimal
    gift_points: Decimal
    actual_payment_cents: int


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value.isoformat() if hasattr(value, "isoformat") else value


async def _collect_plans(db) -> tuple[list[BackfillPlan], list[dict[str, Any]]]:
    """Find every orphan-balance user and pick its mount order.

    An orphan is a user that owns a wallet, has no credit_lots at all, carries a
    non-zero recharge+gift balance, and has at least one completed order to
    attach the reconciliation lot to.  Users without a completed order are
    reported as skipped (their balance cannot be surfaced on the order card; the
    settlement fix still needs a lot, so they are surfaced for manual handling).
    """
    # Users that already carry a migration marker or any credit lot are out of
    # scope: the official reconstructor or a prior run of this script already
    # covered them.
    users_with_lots = (
        await db.execute(select(CreditLot.user_id).distinct())
    ).scalars().all()
    users_with_marker = (
        await db.execute(
            select(PointLedgerEntry.reference_id)
            .where(
                PointLedgerEntry.event_type == MARKER_EVENT_TYPE,
                PointLedgerEntry.reference_type == MARKER_REFERENCE_TYPE,
            )
            .distinct()
        )
    ).scalars().all()
    already_covered = set(users_with_lots) | set(users_with_marker)

    # Candidate wallets: non-zero balance and not yet covered.
    candidates = (
        await db.execute(
            select(UserWallet, User)
            .join(User, User.id == UserWallet.user_id)
            .order_by(User.id)
        )
    ).all()

    plans: list[BackfillPlan] = []
    skipped: list[dict[str, Any]] = []
    for wallet, user in candidates:
        if user.id in already_covered:
            continue
        recharge = point_value(wallet.recharge_balance_points)
        gift = point_value(wallet.gift_balance_points)
        if recharge <= 0 and gift <= 0:
            # Zero/negative balance (e.g. historical debt wallets) has nothing
            # to backfill; leave it untouched.
            continue
        # Pick the most recent completed order as the card mount point so the
        # reconciliation lot shows up on the order card.  paid_at NULLs last so a
        # zero-payment test order doesn't crowd out a real one of equal date.
        order = (
            await db.execute(
                select(BillingOrder)
                .where(
                    BillingOrder.user_id == user.id,
                    BillingOrder.status == "completed",
                )
                .order_by(
                    nullslast(BillingOrder.paid_at.desc()),
                    BillingOrder.created_at.desc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if order is None:
            skipped.append(
                {
                    "user_id": user.id,
                    "username": user.username,
                    "reason": "no completed billing order to attach the reconciliation lot",
                    "recharge_balance": float(recharge),
                    "gift_balance": float(gift),
                }
            )
            continue
        plans.append(
            BackfillPlan(
                user_id=user.id,
                username=user.username,
                order_id=order.id,
                order_no=order.order_no,
                product_name=order.product_name,
                paid_at=order.paid_at,
                recharge_points=recharge,
                gift_points=gift,
                actual_payment_cents=int(order.actual_payment_cents or 0),
            )
        )
    return plans, skipped


def _unit_value(actual_payment_cents: int, total_points: Decimal) -> Decimal:
    """Per-point revenue from the mount order's actual payment."""
    if total_points <= 0:
        return Decimal("0")
    return (
        Decimal(actual_payment_cents) / Decimal("100") / total_points
    ).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


async def backfill(*, apply: bool) -> dict[str, Any]:
    cutover = utc_now()
    safe_expiry = add_months(cutover, SAFE_VALIDITY_MONTHS)
    report: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "cutover": cutover,
        "safe_expiry": safe_expiry,
        "plans": [],
        "skipped": [],
        "summary": {
            "backfilled": 0,
            "skipped": 0,
            "lots_created": 0,
            "total_points_backfilled": Decimal("0"),
        },
    }

    async with async_session_factory() as db:
        plans, skipped = await _collect_plans(db)
        report["skipped"] = skipped
        report["summary"]["skipped"] = len(skipped)

        for plan in plans:
            entries = []
            total_points = plan.recharge_points + plan.gift_points
            unit_value = _unit_value(plan.actual_payment_cents, total_points)
            created = 0
            for lot_type, points in (
                ("recharge", plan.recharge_points),
                ("gift", plan.gift_points),
            ):
                if points <= 0:
                    continue
                lot_id = str(uuid4())
                entries.append(
                    {
                        "lot_id": lot_id,
                        "lot_type": lot_type,
                        "points": points,
                    }
                )
                created += 1
                if apply:
                    db.add(
                        CreditLot(
                            id=lot_id,
                            user_id=plan.user_id,
                            external_user_id=None,
                            lot_type=lot_type,
                            source_type="billing_order",
                            source_id=plan.order_id,
                            batch_id=None,
                            initial_points=points,
                            remaining_points=points,
                            unit_value_yuan=unit_value,
                            valid_from=cutover,
                            expires_at=safe_expiry,
                            status="active",
                        )
                    )
            if apply and entries:
                # Idempotency marker.  lot_id is left NULL (the migration covers
                # the whole user, not a single lot); the unique partial index
                # uq_point_ledger_event_reference_lot_nullsafe dedups on
                # (event_type, reference_type, reference_id, COALESCE(lot_id,'')).
                db.add(
                    PointLedgerEntry(
                        id=str(uuid4()),
                        user_id=plan.user_id,
                        event_type=MARKER_EVENT_TYPE,
                        recharge_delta=plan.recharge_points,
                        gift_delta=plan.gift_points,
                        loyalty_delta=0,
                        recharge_after=plan.recharge_points,
                        gift_after=plan.gift_points,
                        loyalty_after=0,
                        lot_id=None,
                        reference_type=MARKER_REFERENCE_TYPE,
                        reference_id=plan.user_id,
                        description=MARKER_DESCRIPTION,
                    )
                )
                await db.flush()
            report["plans"].append(
                {
                    "user_id": plan.user_id,
                    "username": plan.username,
                    "order_id": plan.order_id,
                    "order_no": plan.order_no,
                    "product_name": plan.product_name,
                    "paid_at": plan.paid_at,
                    "recharge_points": plan.recharge_points,
                    "gift_points": plan.gift_points,
                    "unit_value_yuan": unit_value,
                    "lots": entries,
                    "status": "applied" if apply and entries else "planned" if entries else "empty",
                }
            )
            report["summary"]["backfilled"] += 1
            report["summary"]["lots_created"] += created
            report["summary"]["total_points_backfilled"] += total_points

        if apply:
            await db.commit()
        else:
            await db.rollback()
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist the backfill")
    parser.add_argument("--report", type=Path, help="write the JSON reconciliation report")
    args = parser.parse_args()
    report = await backfill(apply=args.apply)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=_json_value)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    asyncio.run(main())
