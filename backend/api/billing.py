"""Billing API routes."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, case, func, select

from backend.api.deps import DBSession, CurrentUser, is_interior_user
from backend.models import BillingOrder, ConsumptionAllocation, ConsumptionRecord, CreditLot, GrantBatch, User
from backend.schemas.billing import (
    ConsumptionListResponse,
    ConsumptionAllocationListResponse,
    ConsumptionAllocationResponse,
    ConsumptionResponse,
    CouponRedeemRequest,
    CouponRedeemResponse,
    CouponResponse,
    OrderCreateRequest,
    OrderListResponse,
    OrderPreviewRequest,
    OrderPreviewResponse,
    OrderResponse,
    OrderStatusResponse,
    PackageResponse,
    PaymentQrResponse,
    WalletResponse,
)
from backend.services.billing import (
    complete_order,
    create_order,
    ensure_wallet,
    list_runtime_packages,
    preview_order,
)
from backend.services.operate_coupons import bind_coupon_by_code, list_user_coupons
from backend.services import operate_recharge
from backend.utils.time_utils import utc_now

router = APIRouter(prefix="/billing", tags=["Billing"])


def _order_response(
    order: BillingOrder,
    *,
    username: str | None = None,
    enterprise_name: str | None = None,
    consumed_points: float = 0,
    remaining_points: float = 0,
    raw_remaining_points: float = 0,
    points_expires_at: datetime | None = None,
) -> OrderResponse:
    # 仅已完成订单有余额快照（balance_after_wen）；未付费/已取消订单未发生余额变动 → None（前端显示 "-"）
    return OrderResponse(
        id=order.id,
        order_no=order.order_no,
        source="recharge",
        product_name=order.product_name,
        created_at=order.created_at,
        status=order.status,
        order_amount_cents=order.order_amount_cents,
        actual_payment_cents=order.actual_payment_cents,
        coupon_code=order.coupon_code,
        coupon_amount_cents=order.coupon_amount_cents,
        points_used=order.points_used,
        points_amount_cents=order.points_amount_cents,
        expires_at=order.expires_at,
        paid_at=order.paid_at,
        balance_after_wen=order.balance_after_wen,
        current_balance_wen=order.balance_after_wen,
        username=username,
        enterprise_name=enterprise_name,
        recharge_points=float(order.recharge_points or 0),
        gift_points=float(order.gift_points or 0),
        total_points=float(order.total_points or 0),
        recharge_balance_after=float(order.recharge_balance_after) if order.recharge_balance_after is not None else None,
        gift_balance_after=float(order.gift_balance_after) if order.gift_balance_after is not None else None,
        unit_value_yuan=float(order.unit_value_yuan) if order.unit_value_yuan is not None else None,
        validity_months=order.validity_months,
        coupon_benefit_type=order.coupon_benefit_type,
        coupon_gift_points=float(order.coupon_gift_points or 0),
        consumed_points=float(consumed_points or 0),
        remaining_points=float(remaining_points or 0),
        points_expires_at=points_expires_at,
        points_status=_order_points_status(
            order,
            remaining_points=float(remaining_points or 0),
            raw_remaining_points=float(raw_remaining_points or 0),
            points_expires_at=points_expires_at,
        ),
    )


def _order_points_status(
    order: BillingOrder,
    *,
    remaining_points: float,
    raw_remaining_points: float,
    points_expires_at: datetime | None,
) -> str:
    if order.status != "completed":
        return "not_credited"
    if remaining_points > 0 and (points_expires_at is None or points_expires_at > utc_now()):
        return "active"
    if raw_remaining_points > 0 and points_expires_at is not None and points_expires_at <= utc_now():
        return "expired"
    return "exhausted"


def _grant_lot_points_status(lot: CreditLot, *, remaining: float) -> str:
    """赠送点数批次的点数状态映射（CreditLot.status → OrderResponse.points_status）。"""
    if lot.status == "active" and remaining > 0:
        return "active"
    if lot.status == "expired":
        return "expired"
    # exhausted / stopped / 其他均视为已用完（stopped 的剩余点数已失效）
    return "exhausted"


def _grant_lot_response(
    lot: CreditLot,
    batch: GrantBatch | None,
    *,
    username: str | None = None,
    enterprise_name: str | None = None,
) -> OrderResponse:
    """把运营赠送点数批次（CreditLot, source_type='grant_batch'）映射为订单记录行，来源=赠送。"""
    initial = float(lot.initial_points or 0)
    remaining_raw = float(lot.remaining_points or 0)
    stopped = lot.status == "stopped"
    remaining = 0.0 if stopped else remaining_raw
    consumed = initial - remaining
    return OrderResponse(
        id=lot.id,
        order_no=None,
        source="gift",
        product_name=batch.name if batch is not None else "运营赠送",
        created_at=lot.valid_from,
        status="cancelled" if stopped else "completed",
        order_amount_cents=0,
        actual_payment_cents=0,
        coupon_code=None,
        coupon_amount_cents=0,
        points_used=0,
        points_amount_cents=0,
        expires_at=lot.expires_at,
        paid_at=lot.valid_from,
        balance_after_wen=None,
        current_balance_wen=None,
        username=username,
        enterprise_name=enterprise_name,
        recharge_points=0,
        gift_points=initial,
        total_points=initial,
        recharge_balance_after=None,
        gift_balance_after=None,
        unit_value_yuan=float(lot.unit_value_yuan or 0),
        validity_months=0,
        coupon_benefit_type=None,
        coupon_gift_points=0,
        consumed_points=consumed,
        remaining_points=remaining,
        points_expires_at=lot.expires_at,
        points_status=_grant_lot_points_status(lot, remaining=remaining),
    )


@router.get("/wallet", response_model=WalletResponse)
async def get_wallet(db: DBSession, current_user: CurrentUser) -> WalletResponse:
    wallet = await ensure_wallet(db, current_user.id, for_update=True)
    from backend.services.sales import expire_user_lots, get_sales_config
    await expire_user_lots(db, wallet)
    config = await get_sales_config(db)
    recharge = float(wallet.recharge_balance_points)
    gift = float(wallet.gift_balance_points)
    total = recharge + gift
    threshold = float(config.low_balance_threshold)
    return WalletResponse(
        balance_wen=wallet.balance_wen,
        points=wallet.points,
        recharge_balance_points=recharge,
        gift_balance_points=gift,
        total_balance_points=total,
        low_balance_threshold=threshold,
        low_balance=total < threshold,
    )


@router.get("/packages", response_model=list[PackageResponse])
async def get_packages(db: DBSession) -> list[PackageResponse]:
    return await list_runtime_packages(db)


@router.get("/coupons", response_model=list[CouponResponse])
async def get_coupons(current_user: CurrentUser) -> list[CouponResponse]:
    return await list_user_coupons(current_user.username, include_all=True)


@router.post("/coupons/redeem", response_model=CouponRedeemResponse)
async def redeem_coupon(
    body: CouponRedeemRequest,
    current_user: CurrentUser,
) -> CouponRedeemResponse:
    code = body.code.strip()
    coupons = await list_user_coupons(current_user.username, include_all=True)
    redeemed = next(
        (coupon for coupon in coupons if (coupon.code or "").strip().lower() == code.lower()),
        None,
    )
    if redeemed is not None:
        return CouponRedeemResponse(coupon=redeemed, coupons=coupons)

    customer_name = current_user.nickname or current_user.username
    await bind_coupon_by_code(current_user.username, customer_name, code)
    coupons = await list_user_coupons(current_user.username, include_all=True)
    redeemed = next(
        (coupon for coupon in coupons if (coupon.code or "").strip().lower() == code.lower()),
        None,
    )
    return CouponRedeemResponse(coupon=redeemed, coupons=coupons)


@router.post("/orders/preview", response_model=OrderPreviewResponse)
async def preview_recharge_order(
    body: OrderPreviewRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> OrderPreviewResponse:
    return await preview_order(
        db,
        current_user,
        package_code=body.package_code,
        coupon_id=body.coupon_id,
        use_points=body.use_points,
    )


@router.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_recharge_order(
    body: OrderCreateRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> OrderResponse:
    if not body.accepted_agreement:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先同意用户协议")
    order = await create_order(
        db,
        current_user,
        package_code=body.package_code,
        coupon_id=body.coupon_id,
        use_points=body.use_points,
    )
    await db.refresh(order)
    return _order_response(order)


@router.get("/orders", response_model=OrderListResponse)
async def list_orders(
    db: DBSession,
    current_user: CurrentUser,
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    product_name: str | None = Query(None),
    username: str | None = Query(None),
    enterprise_name: str | None = Query(None),
) -> OrderListResponse:
    interior = is_interior_user(current_user)
    now = utc_now()
    lot_summary = (
        select(
            CreditLot.source_id.label("order_id"),
            func.coalesce(func.sum(CreditLot.initial_points - CreditLot.remaining_points), 0).label("consumed_points"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                CreditLot.status == "active",
                                CreditLot.expires_at > now,
                                CreditLot.remaining_points > 0,
                            ),
                            CreditLot.remaining_points,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("remaining_points"),
            func.coalesce(func.sum(CreditLot.remaining_points), 0).label("raw_remaining_points"),
            func.max(CreditLot.expires_at).label("points_expires_at"),
        )
        .where(CreditLot.source_type == "billing_order")
        .group_by(CreditLot.source_id)
        .subquery()
    )
    # 内部用户看全站（JOIN users 取归属）；外部用户只看自己的（归属恒为本人，但同样 JOIN 以统一返回结构）
    stmt = (
        select(
            BillingOrder,
            User.username,
            User.enterprise_name,
            lot_summary.c.consumed_points,
            lot_summary.c.remaining_points,
            lot_summary.c.raw_remaining_points,
            lot_summary.c.points_expires_at,
        )
        .join(User, User.id == BillingOrder.user_id, isouter=True)
        .outerjoin(lot_summary, lot_summary.c.order_id == BillingOrder.id)
    )
    if not interior:
        stmt = stmt.where(BillingOrder.user_id == current_user.id)
    if start_date:
        stmt = stmt.where(BillingOrder.created_at >= start_date)
    if end_date:
        stmt = stmt.where(BillingOrder.created_at <= end_date)
    if product_name:
        stmt = stmt.where(BillingOrder.product_name.ilike(f"%{product_name}%"))
    # 归属筛选仅对内部用户生效（外部已被 user_id 锁定到自己）
    if interior and username:
        stmt = stmt.where(User.username.ilike(f"%{username}%"))
    if interior and enterprise_name:
        stmt = stmt.where(User.enterprise_name.ilike(f"%{enterprise_name}%"))
    available_first = case(
        (
            and_(
                BillingOrder.status == "completed",
                func.coalesce(lot_summary.c.remaining_points, 0) > 0,
            ),
            0,
        ),
        else_=1,
    )
    stmt = stmt.order_by(available_first, BillingOrder.created_at.desc())

    rows = (await db.execute(stmt)).all()
    billing_orders = [
        _order_response(
            order,
            username=u_name,
            enterprise_name=ent_name,
            consumed_points=consumed,
            remaining_points=remaining,
            raw_remaining_points=raw_remaining,
            points_expires_at=point_expiry,
        )
        for order, u_name, ent_name, consumed, remaining, raw_remaining, point_expiry in rows
    ]

    # 运营赠送批次产生的点数（CreditLot, source_type='grant_batch'）并入订单记录，来源标“赠送”
    grant_stmt = (
        select(CreditLot, GrantBatch, User.username, User.enterprise_name)
        .join(GrantBatch, GrantBatch.id == CreditLot.batch_id, isouter=True)
        .join(User, User.id == CreditLot.user_id, isouter=True)
        .where(CreditLot.source_type == "grant_batch")
    )
    if not interior:
        grant_stmt = grant_stmt.where(CreditLot.user_id == current_user.id)
    if start_date:
        grant_stmt = grant_stmt.where(CreditLot.valid_from >= start_date)
    if end_date:
        grant_stmt = grant_stmt.where(CreditLot.valid_from <= end_date)
    if product_name:
        grant_stmt = grant_stmt.where(GrantBatch.name.ilike(f"%{product_name}%"))
    # 归属筛选仅对内部用户生效
    if interior and username:
        grant_stmt = grant_stmt.where(User.username.ilike(f"%{username}%"))
    if interior and enterprise_name:
        grant_stmt = grant_stmt.where(User.enterprise_name.ilike(f"%{enterprise_name}%"))
    grant_stmt = grant_stmt.order_by(CreditLot.valid_from.desc())
    grant_rows = (await db.execute(grant_stmt)).all()
    grant_orders = [
        _grant_lot_response(lot, batch, username=u_name, enterprise_name=ent_name)
        for lot, batch, u_name, ent_name in grant_rows
    ]

    orders = billing_orders + grant_orders
    # 统一排序：可用点数优先，然后时间倒序（稳定排序：先按时间倒序，再按可用优先）
    orders.sort(key=lambda o: o.created_at, reverse=True)
    orders.sort(key=lambda o: 0 if (o.points_status == "active" and o.remaining_points > 0) else 1)
    return OrderListResponse(orders=orders)


@router.get("/orders/{order_id}/pay-qrcode", response_model=PaymentQrResponse)
async def get_pay_qrcode(
    order_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> PaymentQrResponse:
    result = await db.execute(
        select(BillingOrder).where(
            BillingOrder.id == order_id,
            BillingOrder.user_id == current_user.id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")

    # 首次取码时向真实交行下单并缓存 payMerTranNo + 二维码文本，重复请求复用。
    if not order.external_order_no or not order.external_qr_payload:
        created = await operate_recharge.create_recharge_order(
            total_amount_yuan=f"{order.actual_payment_cents / 100:.2f}",
            package_name=order.product_name,
            external_ref=order.order_no,
        )
        order.external_order_no = created["pay_mer_tran_no"]
        order.external_qr_payload = created["display_code_text"]
        await db.flush()
    return PaymentQrResponse(
        order_id=order.id,
        order_no=order.order_no,
        actual_payment_cents=order.actual_payment_cents,
        qr_payload=order.external_qr_payload,
        expires_at=order.expires_at,
    )


@router.get("/orders/{order_id}/status", response_model=OrderStatusResponse)
async def get_order_status(
    order_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> OrderStatusResponse:
    result = await db.execute(
        select(BillingOrder).where(
            BillingOrder.id == order_id,
            BillingOrder.user_id == current_user.id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")

    # 真实交行支付：订单仍 pending 时主动查交行网关，SUCCESS 即出账加点；过期则置 cancelled
    if (
        order.status == "pending"
        and order.external_order_no
    ):
        if order.expires_at < utc_now():
            order.status = "cancelled"
            if order.coupon_id is not None:
                from backend.services.operate_coupons import release_coupon
                await release_coupon(order.coupon_id, order.order_no)
            await db.flush()
        elif await operate_recharge.query_order_status(order.external_order_no) == "success":
            # complete_order consistently locks order -> wallet, avoiding a
            # deadlock with the background payment poller.
            await complete_order(db, current_user, order)
            await db.flush()

    return OrderStatusResponse(
        order_id=order.id,
        order_no=order.order_no,
        status=order.status,
        paid_at=order.paid_at,
        balance_after_wen=order.balance_after_wen,
    )


@router.get("/consumptions", response_model=ConsumptionListResponse)
async def list_consumptions(
    db: DBSession,
    current_user: CurrentUser,
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    project_name: str | None = Query(None),
    username: str | None = Query(None),
    enterprise_name: str | None = Query(None),
) -> ConsumptionListResponse:
    interior = is_interior_user(current_user)
    # 内部用户看全站（JOIN users 取归属）；外部用户只看自己的
    stmt = select(ConsumptionRecord, User.username, User.enterprise_name).join(
        User, User.id == ConsumptionRecord.user_id, isouter=True
    )
    if not interior:
        stmt = stmt.where(ConsumptionRecord.user_id == current_user.id)
    if start_date:
        stmt = stmt.where(ConsumptionRecord.created_at >= start_date)
    if end_date:
        stmt = stmt.where(ConsumptionRecord.created_at <= end_date)
    if project_name:
        stmt = stmt.where(ConsumptionRecord.project_name.ilike(f"%{project_name}%"))
    if interior and username:
        stmt = stmt.where(User.username.ilike(f"%{username}%"))
    if interior and enterprise_name:
        stmt = stmt.where(User.enterprise_name.ilike(f"%{enterprise_name}%"))
    stmt = stmt.order_by(ConsumptionRecord.created_at.desc())
    rows = (await db.execute(stmt)).all()
    consumption_ids = [row.id for row, _, _ in rows]
    # 批量查询每条消费扣点所消耗的充值订单编号（经 consumption_allocations→credit_lots→billing_orders 聚合，避免 N+1）
    order_nos_by_consumption: dict[str, list[str]] = {}
    if consumption_ids:
        order_rows = (
            await db.execute(
                select(ConsumptionAllocation.consumption_id, BillingOrder.order_no)
                .join(CreditLot, CreditLot.id == ConsumptionAllocation.lot_id)
                .join(BillingOrder, BillingOrder.id == CreditLot.source_id)
                .where(
                    ConsumptionAllocation.consumption_id.in_(consumption_ids),
                    CreditLot.source_type == "billing_order",
                )
                .distinct()
                .order_by(ConsumptionAllocation.consumption_id, BillingOrder.order_no)
            )
        ).all()
        for cid, order_no in order_rows:
            if order_no:
                order_nos_by_consumption.setdefault(cid, []).append(order_no)
    return ConsumptionListResponse(
        consumptions=[
            ConsumptionResponse(
                id=row.id,
                consumed_at=row.created_at,
                project_name=row.project_name,
                task_type=row.task_type,
                task_status=row.task_status,
                consumed_wen=row.consumed_wen,
                earned_points=row.earned_points,
                used_by=row.used_by,
                cost_cny=float(row.cost_cny) if row.cost_cny is not None else None,
                username=u_name,
                enterprise_name=ent_name,
                cost_points=float(row.cost_points) if row.cost_points is not None else None,
                sales_multiplier=float(row.sales_multiplier) if row.sales_multiplier is not None else None,
                sales_points=float(row.sales_points) if row.sales_points is not None else None,
                gift_points_used=float(row.gift_points_used or 0),
                recharge_points_used=float(row.recharge_points_used or 0),
                recharge_balance_before=float(row.recharge_balance_before) if row.recharge_balance_before is not None else None,
                gift_balance_before=float(row.gift_balance_before) if row.gift_balance_before is not None else None,
                recharge_balance_after=float(row.recharge_balance_after) if row.recharge_balance_after is not None else None,
                gift_balance_after=float(row.gift_balance_after) if row.gift_balance_after is not None else None,
                settlement_order_nos=", ".join(order_nos_by_consumption.get(row.id, [])) or None,
                weighted_unit_value_yuan=float(row.weighted_unit_value_yuan) if row.weighted_unit_value_yuan is not None else None,
                folded_income_yuan=float(row.folded_income_yuan) if row.folded_income_yuan is not None else None,
                profit_yuan=float(row.profit_yuan) if row.profit_yuan is not None else None,
                profit_margin=float(row.profit_margin) if row.profit_margin is not None else None,
            )
            for row, u_name, ent_name in rows
        ]
    )


@router.get(
    "/consumptions/{consumption_id}/allocations",
    response_model=ConsumptionAllocationListResponse,
)
async def get_consumption_allocations(
    consumption_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ConsumptionAllocationListResponse:
    record = (
        await db.execute(select(ConsumptionRecord).where(ConsumptionRecord.id == consumption_id))
    ).scalar_one_or_none()
    if record is None or (
        record.user_id != current_user.id and not is_interior_user(current_user)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消费记录不存在")

    rows = (
        await db.execute(
            select(ConsumptionAllocation, CreditLot)
            .outerjoin(CreditLot, CreditLot.id == ConsumptionAllocation.lot_id)
            .where(ConsumptionAllocation.consumption_id == consumption_id)
            .order_by(ConsumptionAllocation.created_at, ConsumptionAllocation.id)
        )
    ).all()
    return ConsumptionAllocationListResponse(
        allocations=[
            ConsumptionAllocationResponse(
                id=allocation.id,
                lot_id=allocation.lot_id,
                lot_type=allocation.lot_type,
                source_type=lot.source_type if lot else "overdraft",
                source_id=lot.source_id if lot else record.task_id,
                points=float(allocation.points),
                unit_value_yuan=float(allocation.unit_value_yuan),
                folded_income_yuan=float(allocation.folded_income_yuan),
                expires_at=lot.expires_at if lot else None,
            )
            for allocation, lot in rows
        ]
    )
