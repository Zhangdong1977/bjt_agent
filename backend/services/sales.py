"""Sales settings, point lots, grants and allocation helpers."""

import calendar
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from fastapi import HTTPException, status
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    ConsumptionAllocation,
    CreditLot,
    GrantBatch,
    PointLedgerEntry,
    SalesConfig,
    SalesPackage,
    User,
    UserWallet,
)
from backend.schemas.sales import GrantCreatePayload, SalesSnapshotPayload
from backend.utils.time_utils import utc_now


POINT_QUANT = Decimal("0.01")
MONEY_QUANT = Decimal("0.000001")


def decimal_value(value) -> Decimal:
    return Decimal(str(value or 0))


def point_value(value) -> Decimal:
    return decimal_value(value).quantize(POINT_QUANT, rounding=ROUND_HALF_UP)


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


DEFAULT_PACKAGES = (
    ("experience", "体验套餐", 3000, "300", "50", "plan-icon-trial", "500页以上标书谨慎使用", 10),
    ("basic", "基础套餐", 10000, "1000", "200", "plan-icon-basic", None, 20),
    ("premium", "尊享套餐", 30000, "3000", "1000", "plan-icon-premium", None, 30),
    ("luxury", "豪华套餐", 100000, "10000", "5000", "plan-icon-luxury", None, 40),
)


async def ensure_sales_defaults(db: AsyncSession) -> SalesConfig:
    config = await db.get(SalesConfig, "default")
    if config is None:
        config = SalesConfig(
            id="default", sales_multiplier=Decimal("4"), low_balance_threshold=Decimal("0"), config_version=1
        )
        db.add(config)
        await db.flush()

    count = (await db.execute(select(func.count()).select_from(SalesPackage))).scalar_one()
    if not count:
        for code, name, cents, recharge, gift, icon, caution, sort_order in DEFAULT_PACKAGES:
            db.add(
                SalesPackage(
                    code=code,
                    name=name,
                    amount_cents=cents,
                    recharge_points=Decimal(recharge),
                    gift_points=Decimal(gift),
                    validity_months=12,
                    loyalty_deduction_limit=None,
                    is_online=True,
                    sort_order=sort_order,
                    icon_url=icon,
                    caution=caution,
                    config_version=1,
                )
            )
        await db.flush()
    return config


async def get_sales_config(db: AsyncSession) -> SalesConfig:
    return await ensure_sales_defaults(db)


async def list_sales_packages(db: AsyncSession, *, online_only: bool = True) -> list[SalesPackage]:
    await ensure_sales_defaults(db)
    stmt = select(SalesPackage)
    if online_only:
        stmt = stmt.where(SalesPackage.is_online.is_(True))
    return list((await db.execute(stmt.order_by(SalesPackage.sort_order, SalesPackage.created_at))).scalars().all())


async def get_sales_package(db: AsyncSession, code: str, *, require_online: bool = True) -> SalesPackage:
    await ensure_sales_defaults(db)
    stmt = select(SalesPackage).where(SalesPackage.code == code)
    if require_online:
        stmt = stmt.where(SalesPackage.is_online.is_(True))
    package = (await db.execute(stmt)).scalar_one_or_none()
    if package is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="套餐不存在或已下线")
    return package


async def apply_sales_snapshot(db: AsyncSession, payload: SalesSnapshotPayload) -> SalesConfig:
    config = await ensure_sales_defaults(db)
    incoming_version = payload.config.version
    if incoming_version < config.config_version:
        raise HTTPException(status_code=409, detail="配置版本低于当前生效版本")

    config.sales_multiplier = Decimal(str(payload.config.sales_multiplier))
    config.low_balance_threshold = point_value(payload.config.low_balance_threshold)
    config.config_version = incoming_version

    existing = {
        row.code: row
        for row in (await db.execute(select(SalesPackage).with_for_update())).scalars().all()
    }
    incoming_codes: set[str] = set()
    for item in payload.packages:
        incoming_codes.add(item.code)
        row = existing.get(item.code)
        if row is None:
            row = SalesPackage(code=item.code)
            db.add(row)
        row.name = item.name
        row.icon_url = item.icon_url
        row.amount_cents = item.amount_cents
        row.recharge_points = point_value(item.recharge_points)
        row.gift_points = point_value(item.gift_points)
        row.validity_months = item.validity_months
        row.loyalty_deduction_limit = item.loyalty_deduction_limit
        row.is_online = item.is_online
        row.sort_order = item.sort_order
        row.caution = item.caution
        row.config_version = incoming_version

    for code, row in existing.items():
        if code not in incoming_codes:
            row.is_online = False
            row.config_version = incoming_version
    await db.flush()
    return config


def sync_legacy_balance(wallet: UserWallet) -> None:
    wallet.balance_wen = int(
        (decimal_value(wallet.recharge_balance_points) + decimal_value(wallet.gift_balance_points)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


async def _write_ledger(
    db: AsyncSession,
    wallet: UserWallet,
    *,
    event_type: str,
    recharge_delta: Decimal = Decimal("0"),
    gift_delta: Decimal = Decimal("0"),
    loyalty_delta: int = 0,
    lot_id: str | None,
    reference_type: str,
    reference_id: str,
    description: str,
) -> None:
    db.add(
        PointLedgerEntry(
            user_id=wallet.user_id,
            event_type=event_type,
            recharge_delta=recharge_delta,
            gift_delta=gift_delta,
            loyalty_delta=loyalty_delta,
            recharge_after=point_value(wallet.recharge_balance_points),
            gift_after=point_value(wallet.gift_balance_points),
            loyalty_after=wallet.points,
            lot_id=lot_id,
            reference_type=reference_type,
            reference_id=reference_id,
            description=description,
        )
    )


async def add_credit_lot(
    db: AsyncSession,
    wallet: UserWallet,
    *,
    lot_type: str,
    source_type: str,
    source_id: str,
    points: Decimal,
    unit_value_yuan: Decimal,
    expires_at: datetime,
    external_user_id: int | None = None,
    batch_id: str | None = None,
    description: str,
) -> CreditLot | None:
    points = point_value(points)
    if points <= 0:
        return None
    existing = (
        await db.execute(
            select(CreditLot).where(
                CreditLot.source_type == source_type,
                CreditLot.source_id == source_id,
                CreditLot.user_id == wallet.user_id,
                CreditLot.lot_type == lot_type,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    lot = CreditLot(
        user_id=wallet.user_id,
        external_user_id=external_user_id,
        lot_type=lot_type,
        source_type=source_type,
        source_id=source_id,
        batch_id=batch_id,
        initial_points=points,
        remaining_points=points,
        unit_value_yuan=unit_value_yuan,
        valid_from=utc_now(),
        expires_at=expires_at,
        status="active",
    )
    db.add(lot)
    await db.flush()
    if lot_type == "gift":
        wallet.gift_balance_points = point_value(decimal_value(wallet.gift_balance_points) + points)
        recharge_delta, gift_delta = Decimal("0"), points
    else:
        wallet.recharge_balance_points = point_value(decimal_value(wallet.recharge_balance_points) + points)
        recharge_delta, gift_delta = points, Decimal("0")
    sync_legacy_balance(wallet)
    await _write_ledger(
        db,
        wallet,
        event_type="credit",
        recharge_delta=recharge_delta,
        gift_delta=gift_delta,
        lot_id=lot.id,
        reference_type=source_type,
        reference_id=source_id,
        description=description,
    )
    return lot


async def expire_user_lots(db: AsyncSession, wallet: UserWallet) -> int:
    now = utc_now()
    lots = list(
        (
            await db.execute(
                select(CreditLot)
                .where(
                    CreditLot.user_id == wallet.user_id,
                    CreditLot.status == "active",
                    CreditLot.expires_at <= now,
                    CreditLot.remaining_points > 0,
                )
                .with_for_update()
            )
        ).scalars().all()
    )
    for lot in lots:
        remaining = point_value(lot.remaining_points)
        lot.status = "expired"
        if lot.lot_type == "gift":
            wallet.gift_balance_points = point_value(decimal_value(wallet.gift_balance_points) - remaining)
            recharge_delta, gift_delta = Decimal("0"), -remaining
        else:
            # A negative recharge balance is debt and is not represented by a lot.
            wallet.recharge_balance_points = point_value(decimal_value(wallet.recharge_balance_points) - remaining)
            recharge_delta, gift_delta = -remaining, Decimal("0")
        sync_legacy_balance(wallet)
        await _write_ledger(
            db,
            wallet,
            event_type="expire",
            recharge_delta=recharge_delta,
            gift_delta=gift_delta,
            lot_id=lot.id,
            reference_type="credit_lot",
            reference_id=lot.id,
            description="点数到期失效",
        )
    return len(lots)


async def expire_all_due_lots(db: AsyncSession, *, user_limit: int = 500) -> dict[str, int]:
    """Expire due lots in bounded user batches.

    The wallet row is locked before its lots so concurrent settlement and the
    expiry worker always serialize in the same order.
    """
    now = utc_now()
    user_ids = list(
        (
            await db.execute(
                select(CreditLot.user_id)
                .where(
                    CreditLot.status == "active",
                    CreditLot.remaining_points > 0,
                    CreditLot.expires_at <= now,
                )
                .distinct()
                .order_by(CreditLot.user_id)
                .limit(user_limit)
            )
        ).scalars().all()
    )
    expired_lots = 0
    processed_users = 0
    for user_id in user_ids:
        wallet = (
            await db.execute(
                select(UserWallet).where(UserWallet.user_id == user_id).with_for_update()
            )
        ).scalar_one_or_none()
        if wallet is None:
            continue
        expired_lots += await expire_user_lots(db, wallet)
        processed_users += 1
    return {"users": processed_users, "lots": expired_lots}


async def allocate_consumption(
    db: AsyncSession,
    wallet: UserWallet,
    *,
    consumption_id: str,
    sales_points: Decimal,
    cost_yuan: Decimal,
    task_id: str,
) -> dict[str, Decimal | int]:
    """Deduct gift-first/FIFO-expiry and return auditable totals."""
    sales_points = point_value(sales_points)
    await expire_user_lots(db, wallet)
    before_recharge = point_value(wallet.recharge_balance_points)
    before_gift = point_value(wallet.gift_balance_points)
    remaining = sales_points
    allocated: list[tuple[CreditLot | None, str, Decimal, Decimal]] = []

    for lot_type in ("gift", "recharge"):
        if remaining <= 0:
            break
        lots = list(
            (
                await db.execute(
                    select(CreditLot)
                    .where(
                        CreditLot.user_id == wallet.user_id,
                        CreditLot.lot_type == lot_type,
                        CreditLot.status == "active",
                        CreditLot.remaining_points > 0,
                        CreditLot.expires_at > utc_now(),
                    )
                    .order_by(CreditLot.expires_at, CreditLot.created_at, CreditLot.id)
                    .with_for_update()
                )
            ).scalars().all()
        )
        for lot in lots:
            if remaining <= 0:
                break
            take = min(remaining, point_value(lot.remaining_points))
            lot.remaining_points = point_value(decimal_value(lot.remaining_points) - take)
            if point_value(lot.remaining_points) == 0:
                lot.status = "exhausted"
            allocated.append((lot, lot_type, take, decimal_value(lot.unit_value_yuan)))
            remaining -= take

    # Unknown task cost can exceed the available balance.  Only recharge debt
    # is allowed to become negative; gift lots never do.
    if remaining > 0:
        allocated.append((None, "recharge", remaining, Decimal("0")))

    gift_used = sum((points for _, kind, points, _ in allocated if kind == "gift"), Decimal("0"))
    recharge_used = sales_points - gift_used
    # Free gift consumption does not mint loyalty points. Fractional recharge
    # points are rounded down because the legacy loyalty wallet is integer.
    earned_loyalty = int(recharge_used.to_integral_value(rounding=ROUND_DOWN))
    folded_income = sum((points * unit for _, _, points, unit in allocated), Decimal("0")).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    allocated_cost_total = Decimal("0")
    rounded_cost = decimal_value(cost_yuan).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    for index, (lot, kind, points, unit) in enumerate(allocated):
        # Give the final allocation the rounding remainder so the auditable
        # allocation rows always sum exactly to the task cost.
        if index == len(allocated) - 1:
            allocated_cost = rounded_cost - allocated_cost_total
        else:
            allocated_cost = (
                decimal_value(cost_yuan) * points / sales_points
                if sales_points > 0
                else Decimal("0")
            ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
            allocated_cost_total += allocated_cost

        if kind == "gift":
            wallet.gift_balance_points = point_value(
                decimal_value(wallet.gift_balance_points) - points
            )
        else:
            wallet.recharge_balance_points = point_value(
                decimal_value(wallet.recharge_balance_points) - points
            )
        loyalty_delta = earned_loyalty if index == len(allocated) - 1 else 0
        wallet.points += loyalty_delta
        sync_legacy_balance(wallet)

        db.add(
            ConsumptionAllocation(
                consumption_id=consumption_id,
                lot_id=lot.id if lot else None,
                lot_type=kind,
                points=points,
                unit_value_yuan=unit,
                folded_income_yuan=(points * unit).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP),
                allocated_cost_yuan=allocated_cost,
            )
        )
        await _write_ledger(
            db,
            wallet,
            event_type="consume",
            recharge_delta=-points if kind == "recharge" else Decimal("0"),
            gift_delta=-points if kind == "gift" else Decimal("0"),
            loyalty_delta=loyalty_delta,
            lot_id=lot.id if lot else None,
            reference_type="review_task",
            reference_id=task_id,
            description="AI检查消费",
        )

    return {
        "gift_used": point_value(gift_used),
        "recharge_used": point_value(recharge_used),
        "earned_loyalty": earned_loyalty,
        "before_recharge": before_recharge,
        "before_gift": before_gift,
        "after_recharge": point_value(wallet.recharge_balance_points),
        "after_gift": point_value(wallet.gift_balance_points),
        "folded_income": folded_income,
        "weighted_unit_value": (folded_income / sales_points).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        if sales_points > 0
        else Decimal("0"),
    }


async def issue_grant_batch(db: AsyncSession, payload: GrantCreatePayload) -> GrantBatch:
    # Serialize retries carrying the same client-generated key. This closes the
    # race where an HTTP timeout triggers a retry while the first request is
    # still committing, which would otherwise hit the unique constraint.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:idempotency_key))"),
        {"idempotency_key": payload.idempotency_key},
    )
    existing = (
        await db.execute(select(GrantBatch).where(GrantBatch.idempotency_key == payload.idempotency_key))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    unique_recipients = {item.external_user_id: item for item in payload.recipients}
    batch = GrantBatch(
        name=payload.name,
        points_per_account=point_value(payload.points_per_account),
        validity_value=payload.validity_value,
        validity_unit=payload.validity_unit,
        reason=payload.reason,
        remark=payload.remark,
        account_count=len(unique_recipients),
        total_points=point_value(payload.points_per_account) * len(unique_recipients),
        created_by=payload.created_by,
        idempotency_key=payload.idempotency_key,
    )
    db.add(batch)
    await db.flush()

    now = utc_now()
    expires_at = (
        now + timedelta(days=payload.validity_value)
        if payload.validity_unit == "day"
        else add_months(now, payload.validity_value)
    )
    from backend.services.billing import ensure_wallet

    for recipient in unique_recipients.values():
        user = (
            await db.execute(select(User).where(User.external_user_id == recipient.external_user_id))
        ).scalar_one_or_none()
        if user is None:
            user = (await db.execute(select(User).where(User.username == recipient.username))).scalar_one_or_none()
        if (
            user is not None
            and user.external_user_id is not None
            and user.external_user_id != recipient.external_user_id
        ):
            raise HTTPException(status_code=409, detail=f"账号 {recipient.username} 的用户标识不一致，请刷新后重试")
        if user is None:
            user = User(
                username=recipient.username,
                email=f"external-{recipient.external_user_id}@aibjt.internal",
                password_hash="external_auth",
            )
            db.add(user)
            await db.flush()
        user.external_user_id = recipient.external_user_id
        user.nickname = recipient.nickname or user.nickname
        user.enterprise_name = recipient.enterprise_name
        wallet = await ensure_wallet(db, user.id, for_update=True)
        await add_credit_lot(
            db,
            wallet,
            lot_type="gift",
            source_type="grant_batch",
            source_id=batch.id,
            points=point_value(payload.points_per_account),
            unit_value_yuan=Decimal("0"),
            expires_at=expires_at,
            external_user_id=recipient.external_user_id,
            batch_id=batch.id,
            description=f"{payload.name}赠送",
        )
    await db.flush()
    return batch


async def stop_grant_lot(db: AsyncSession, lot_id: str, *, operator: str, reason: str) -> CreditLot:
    candidate = (
        await db.execute(select(CreditLot).where(CreditLot.id == lot_id))
    ).scalar_one_or_none()
    if candidate is None or candidate.source_type != "grant_batch":
        raise HTTPException(status_code=404, detail="赠送权益不存在")
    # Keep the global accounting lock order wallet -> lots. Allocation and
    # expiry use the same order, preventing stop/consume deadlocks.
    wallet = (
        await db.execute(
            select(UserWallet)
            .where(UserWallet.user_id == candidate.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    lot = (
        await db.execute(
            select(CreditLot)
            .where(CreditLot.id == lot_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if lot is None or lot.source_type != "grant_batch":
        raise HTTPException(status_code=404, detail="赠送权益不存在")
    if lot.status == "stopped":
        return lot
    if lot.status not in {"active"} or point_value(lot.remaining_points) <= 0:
        raise HTTPException(status_code=400, detail="当前赠送权益已无法停用")
    remaining = point_value(lot.remaining_points)
    wallet.gift_balance_points = point_value(decimal_value(wallet.gift_balance_points) - remaining)
    sync_legacy_balance(wallet)
    lot.status = "stopped"
    lot.stopped_at = utc_now()
    lot.stopped_by = operator
    lot.stop_reason = reason
    await _write_ledger(
        db,
        wallet,
        event_type="stop",
        gift_delta=-remaining,
        lot_id=lot.id,
        reference_type="credit_lot",
        reference_id=lot.id,
        description=f"赠送权益停用：{reason}",
    )
    return lot


async def grant_summary(db: AsyncSession) -> dict[str, Decimal]:
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(CreditLot.initial_points), 0),
                func.coalesce(func.sum(CreditLot.initial_points - CreditLot.remaining_points), 0),
                func.coalesce(func.sum(case((CreditLot.status == "active", CreditLot.remaining_points), else_=0)), 0),
                func.coalesce(func.sum(case((CreditLot.status == "expired", CreditLot.remaining_points), else_=0)), 0),
                func.coalesce(func.sum(case((CreditLot.status == "stopped", CreditLot.remaining_points), else_=0)), 0),
            ).where(CreditLot.source_type == "grant_batch")
        )
    ).one()
    return {
        "cumulative_points": decimal_value(row[0]),
        "used_points": decimal_value(row[1]),
        "unused_points": decimal_value(row[2]),
        "expired_points": decimal_value(row[3]),
        "voided_points": decimal_value(row[4]),
    }
