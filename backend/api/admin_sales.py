"""Machine-to-machine sales-management API for operate-two."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select

from backend.api.admin import verify_internal_key
from backend.api.deps import DBSession
from backend.models import (
    BillingOrder,
    ConsumptionAllocation,
    ConsumptionRecord,
    CreditLot,
    GrantBatch,
    SalesPackage,
    User,
)
from backend.schemas.sales import GrantCreatePayload, GrantStopPayload, SalesSnapshotPayload
from backend.services.sales import (
    apply_sales_snapshot,
    decimal_value,
    grant_summary,
    issue_grant_batch,
    stop_grant_lot,
)


router = APIRouter(
    prefix="/admin/sales",
    tags=["Admin Sales"],
    dependencies=[Depends(verify_internal_key)],
)


def number(value) -> float:
    return float(decimal_value(value))


@router.put("/snapshot")
async def apply_snapshot(payload: SalesSnapshotPayload, db: DBSession):
    config = await apply_sales_snapshot(db, payload)
    return {"version": config.config_version, "effective": True}


@router.post("/grants")
async def create_grant(payload: GrantCreatePayload, db: DBSession):
    batch = await issue_grant_batch(db, payload)
    return {"id": batch.id, "account_count": batch.account_count, "total_points": number(batch.total_points)}


@router.get("/grants/summary")
async def get_grant_summary(db: DBSession):
    return {key: number(value) for key, value in (await grant_summary(db)).items()}


@router.get("/grants")
async def list_grants(
    db: DBSession,
    name: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    lot_stats = (
        select(
            CreditLot.batch_id.label("batch_id"),
            func.count(func.distinct(case((CreditLot.status == "stopped", CreditLot.id), else_=None))).label("stopped_count"),
            func.count(func.distinct(case((CreditLot.status == "expired", CreditLot.id), else_=None))).label("expired_count"),
            func.count(func.distinct(CreditLot.id)).label("lot_count"),
            func.max(CreditLot.expires_at).label("expires_at"),
            func.coalesce(func.sum(ConsumptionAllocation.allocated_cost_yuan), 0).label("cost_yuan"),
        )
        .outerjoin(ConsumptionAllocation, ConsumptionAllocation.lot_id == CreditLot.id)
        .where(CreditLot.source_type == "grant_batch")
        .group_by(CreditLot.batch_id)
        .subquery()
    )
    stmt = select(GrantBatch, lot_stats).outerjoin(lot_stats, lot_stats.c.batch_id == GrantBatch.id)
    if name:
        stmt = stmt.where(GrantBatch.name.ilike(f"%{name}%"))
    if start_time:
        stmt = stmt.where(GrantBatch.created_at >= start_time)
    if end_time:
        stmt = stmt.where(GrantBatch.created_at <= end_time)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(GrantBatch.created_at.desc()).offset(offset).limit(limit))).all()
    items = []
    for (
        batch,
        _batch_id,
        stopped_count_raw,
        _expired_count,
        lot_count_raw,
        expires_at,
        generated_cost,
    ) in rows:
        stopped_count = int(stopped_count_raw or 0)
        lot_count = int(lot_count_raw or batch.account_count)
        termination = "全部终止" if lot_count and stopped_count == lot_count else "部分终止" if stopped_count else "未终止"
        items.append(
            {
                "id": batch.id,
                "name": batch.name,
                "created_at": batch.created_at,
                "account_count": batch.account_count,
                "points_per_account": number(batch.points_per_account),
                "folded_value_per_account_yuan": number(batch.points_per_account) / 10,
                "total_points": number(batch.total_points),
                "total_value_yuan": number(batch.total_points) / 10,
                "validity_value": batch.validity_value,
                "validity_unit": batch.validity_unit,
                "expires_at": expires_at,
                "reason": batch.reason,
                "remark": batch.remark,
                "termination_status": termination,
                "generated_cost_yuan": number(generated_cost),
            }
        )
    return {"items": items, "total": total}


@router.get("/grants/{batch_id}")
async def grant_details(batch_id: str, db: DBSession):
    cost_stats = (
        select(
            ConsumptionAllocation.lot_id,
            func.coalesce(func.sum(ConsumptionAllocation.allocated_cost_yuan), 0).label("cost_yuan"),
        )
        .group_by(ConsumptionAllocation.lot_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(CreditLot, User, cost_stats.c.cost_yuan)
            .join(User, User.id == CreditLot.user_id)
            .outerjoin(cost_stats, cost_stats.c.lot_id == CreditLot.id)
            .where(CreditLot.batch_id == batch_id)
            .order_by(User.username)
        )
    ).all()
    return {
        "items": [
            {
                "lot_id": lot.id,
                "external_user_id": lot.external_user_id,
                "username": user.username,
                "nickname": user.nickname,
                "initial_points": number(lot.initial_points),
                "used_points": number(decimal_value(lot.initial_points) - decimal_value(lot.remaining_points)),
                "remaining_points": number(lot.remaining_points),
                "status": lot.status,
                "expires_at": lot.expires_at,
                "stopped_at": lot.stopped_at,
                "stop_reason": lot.stop_reason,
                "generated_cost_yuan": number(cost_yuan),
            }
            for lot, user, cost_yuan in rows
        ]
    }


@router.post("/grants/lots/{lot_id}/stop")
async def stop_grant(lot_id: str, payload: GrantStopPayload, db: DBSession):
    lot = await stop_grant_lot(db, lot_id, operator=payload.operator, reason=payload.reason)
    return {"lot_id": lot.id, "status": lot.status, "remaining_points": number(lot.remaining_points)}


def _date_bucket(column, grain: str):
    localized = func.timezone("Asia/Shanghai", column)
    return func.date_trunc("month" if grain == "month" else "day", localized)


LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _default_range(grain: str, start_time: datetime | None, end_time: datetime | None):
    now = datetime.now(LOCAL_TZ)
    if end_time is None:
        end_time = now.astimezone(timezone.utc)
    if start_time is None:
        if grain == "month":
            month_index = now.year * 12 + now.month - 1 - 11
            local_start = datetime(month_index // 12, month_index % 12 + 1, 1, tzinfo=LOCAL_TZ)
        else:
            local_start = (now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
        start_time = local_start.astimezone(timezone.utc)
    return start_time, end_time


def _period_key(value, grain: str) -> str:
    if isinstance(value, str):
        return value[:7] if grain == "month" else value[:10]
    return value.strftime("%Y-%m" if grain == "month" else "%Y-%m-%d")


def _period_keys(start_time: datetime, end_time: datetime, grain: str) -> list[str]:
    start = start_time.astimezone(LOCAL_TZ)
    end = end_time.astimezone(LOCAL_TZ)
    keys = []
    if grain == "month":
        cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cursor <= end:
            keys.append(cursor.strftime("%Y-%m"))
            month_index = cursor.year * 12 + cursor.month
            cursor = cursor.replace(year=month_index // 12, month=month_index % 12 + 1)
    else:
        cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while cursor.date() <= end.date():
            keys.append(cursor.strftime("%Y-%m-%d"))
            cursor += timedelta(days=1)
    return keys


@router.get("/stats/recharge")
async def recharge_stats(
    db: DBSession,
    grain: str = Query("day", pattern="^(day|month)$"),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
):
    start_time, end_time = _default_range(grain, start_time, end_time)
    base = [BillingOrder.status == "completed"]
    if start_time:
        base.append(BillingOrder.paid_at >= start_time)
    if end_time:
        base.append(BillingOrder.paid_at <= end_time)
    bucket = _date_bucket(BillingOrder.paid_at, grain).label("period")
    rows = (
        await db.execute(
            select(bucket, func.sum(BillingOrder.actual_payment_cents), func.count(BillingOrder.id))
            .where(*base)
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()
    now_local = func.timezone("Asia/Shanghai", func.now())
    overview = (
        await db.execute(
            select(
                func.coalesce(func.sum(case((func.date(_date_bucket(BillingOrder.paid_at, "day")) == func.date(now_local), BillingOrder.actual_payment_cents), else_=0)), 0),
                func.coalesce(func.sum(case((func.date_trunc("month", func.timezone("Asia/Shanghai", BillingOrder.paid_at)) == func.date_trunc("month", now_local), BillingOrder.actual_payment_cents), else_=0)), 0),
                func.coalesce(func.sum(BillingOrder.actual_payment_cents), 0),
            ).where(BillingOrder.status == "completed")
        )
    ).one()
    row_map = {
        _period_key(period, grain): {"amount_yuan": number(cents) / 100, "count": count}
        for period, cents, count in rows
    }
    return {
        "overview": {"today_amount_yuan": number(overview[0]) / 100, "month_amount_yuan": number(overview[1]) / 100, "total_amount_yuan": number(overview[2]) / 100},
        "range": {"start": start_time, "end": end_time, "grain": grain},
        "series": [
            {"period": key, **row_map.get(key, {"amount_yuan": 0, "count": 0})}
            for key in _period_keys(start_time, end_time, grain)
        ],
    }


@router.get("/stats/consumption")
async def consumption_stats(
    db: DBSession,
    grain: str = Query("day", pattern="^(day|month)$"),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
):
    start_time, end_time = _default_range(grain, start_time, end_time)
    filters = []
    if start_time:
        filters.append(ConsumptionRecord.created_at >= start_time)
    if end_time:
        filters.append(ConsumptionRecord.created_at <= end_time)
    bucket = _date_bucket(ConsumptionRecord.created_at, grain).label("period")
    rows = (
        await db.execute(
            select(
                bucket,
                func.coalesce(func.sum(ConsumptionRecord.folded_income_yuan), 0),
                func.coalesce(func.sum(ConsumptionRecord.cost_cny), 0),
                func.coalesce(func.sum(ConsumptionRecord.profit_yuan), 0),
            )
            .where(*filters)
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()
    now_local = func.timezone("Asia/Shanghai", func.now())
    record_local = func.timezone("Asia/Shanghai", ConsumptionRecord.created_at)
    today_condition = func.date(record_local) == func.date(now_local)
    month_condition = func.date_trunc("month", record_local) == func.date_trunc("month", now_local)
    overview = (
        await db.execute(
            select(
                func.coalesce(func.sum(case((today_condition, ConsumptionRecord.folded_income_yuan), else_=0)), 0),
                func.coalesce(func.sum(case((today_condition, ConsumptionRecord.cost_cny), else_=0)), 0),
                func.coalesce(func.sum(case((today_condition, ConsumptionRecord.profit_yuan), else_=0)), 0),
                func.coalesce(func.sum(case((month_condition, ConsumptionRecord.folded_income_yuan), else_=0)), 0),
                func.coalesce(func.sum(case((month_condition, ConsumptionRecord.cost_cny), else_=0)), 0),
                func.coalesce(func.sum(case((month_condition, ConsumptionRecord.profit_yuan), else_=0)), 0),
                func.coalesce(func.sum(ConsumptionRecord.folded_income_yuan), 0),
                func.coalesce(func.sum(ConsumptionRecord.cost_cny), 0),
                func.coalesce(func.sum(ConsumptionRecord.profit_yuan), 0),
            )
        )
    ).one()
    row_map = {
        _period_key(period, grain): {
            "income_yuan": number(income), "cost_yuan": number(cost), "profit_yuan": number(profit)
        }
        for period, income, cost, profit in rows
    }
    return {
        "overview": {
            "today": {"income_yuan": number(overview[0]), "cost_yuan": number(overview[1]), "profit_yuan": number(overview[2])},
            "month": {"income_yuan": number(overview[3]), "cost_yuan": number(overview[4]), "profit_yuan": number(overview[5])},
            "total": {"income_yuan": number(overview[6]), "cost_yuan": number(overview[7]), "profit_yuan": number(overview[8])},
        },
        "range": {"start": start_time, "end": end_time, "grain": grain},
        "series": [
            {"period": key, **row_map.get(key, {"income_yuan": 0, "cost_yuan": 0, "profit_yuan": 0})}
            for key in _period_keys(start_time, end_time, grain)
        ],
    }


@router.get("/details/recharges")
async def recharge_details(
    db: DBSession,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    username: str | None = None,
    min_amount_cents: int | None = None,
    max_amount_cents: int | None = None,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    stmt = select(BillingOrder, User).join(User, User.id == BillingOrder.user_id).where(BillingOrder.status == "completed")
    if start_time:
        stmt = stmt.where(BillingOrder.paid_at >= start_time)
    if end_time:
        stmt = stmt.where(BillingOrder.paid_at <= end_time)
    if username:
        stmt = stmt.where(User.username.ilike(f"%{username}%"))
    if min_amount_cents is not None:
        stmt = stmt.where(BillingOrder.actual_payment_cents >= min_amount_cents)
    if max_amount_cents is not None:
        stmt = stmt.where(BillingOrder.actual_payment_cents <= max_amount_cents)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(BillingOrder.paid_at.desc()).offset(offset).limit(limit))).all()
    return {
        "total": total,
        "items": [
            {
                "id": order.id, "paid_at": order.paid_at, "external_user_id": user.external_user_id,
                "username": user.username, "nickname": user.nickname,
                "recharge_balance_before": number(order.recharge_balance_before), "gift_balance_before": number(order.gift_balance_before),
                "recharge_points": number(order.recharge_points), "gift_points": number(order.gift_points),
                "recharge_balance_after": number(order.recharge_balance_after), "gift_balance_after": number(order.gift_balance_after),
                "actual_payment_yuan": order.actual_payment_cents / 100, "loyalty_points_used": order.points_used,
                "loyalty_deduction_yuan": order.points_amount_cents / 100, "unit_value_yuan": number(order.unit_value_yuan),
                "validity_months": order.validity_months, "product_name": order.product_name,
            }
            for order, user in rows
        ],
    }


@router.get("/details/consumptions")
async def consumption_details(
    db: DBSession,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    username: str | None = None,
    min_points: float | None = None,
    max_points: float | None = None,
    min_amount_yuan: float | None = None,
    max_amount_yuan: float | None = None,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    stmt = select(ConsumptionRecord, User).join(User, User.id == ConsumptionRecord.user_id)
    if start_time:
        stmt = stmt.where(ConsumptionRecord.created_at >= start_time)
    if end_time:
        stmt = stmt.where(ConsumptionRecord.created_at <= end_time)
    if username:
        stmt = stmt.where(User.username.ilike(f"%{username}%"))
    if min_points is not None:
        stmt = stmt.where(ConsumptionRecord.sales_points >= min_points)
    if max_points is not None:
        stmt = stmt.where(ConsumptionRecord.sales_points <= max_points)
    if min_amount_yuan is not None:
        stmt = stmt.where(ConsumptionRecord.folded_income_yuan >= min_amount_yuan)
    if max_amount_yuan is not None:
        stmt = stmt.where(ConsumptionRecord.folded_income_yuan <= max_amount_yuan)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(ConsumptionRecord.created_at.desc()).offset(offset).limit(limit))).all()
    return {
        "total": total,
        "items": [
            {
                "id": record.id, "consumed_at": record.created_at, "external_user_id": user.external_user_id,
                "username": user.username, "nickname": user.nickname, "project_name": record.project_name,
                "recharge_balance_before": number(record.recharge_balance_before), "gift_balance_before": number(record.gift_balance_before),
                "cost_points": number(record.cost_points), "sales_multiplier": number(record.sales_multiplier),
                "sales_points": number(record.sales_points), "gift_points_used": number(record.gift_points_used),
                "recharge_points_used": number(record.recharge_points_used),
                "recharge_balance_after": number(record.recharge_balance_after), "gift_balance_after": number(record.gift_balance_after),
                "weighted_unit_value_yuan": number(record.weighted_unit_value_yuan), "income_yuan": number(record.folded_income_yuan),
                "cost_yuan": number(record.cost_cny), "profit_yuan": number(record.profit_yuan), "profit_margin": number(record.profit_margin),
            }
            for record, user in rows
        ],
    }
