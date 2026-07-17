"""Billing API routes."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from backend.api.deps import DBSession, CurrentUser, is_interior_user
from backend.models import BillingOrder, ConsumptionRecord, User, UserWallet
from backend.schemas.billing import (
    ConsumptionListResponse,
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
    list_packages,
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
) -> OrderResponse:
    # 仅已完成订单有余额快照（balance_after_wen）；未付费/已取消订单未发生余额变动 → None（前端显示 "-"）
    return OrderResponse(
        id=order.id,
        order_no=order.order_no,
        product_name=order.product_name,
        created_at=order.created_at,
        status=order.status,
        order_amount_cents=order.order_amount_cents,
        actual_payment_cents=order.actual_payment_cents,
        coupon_code=order.coupon_code,
        coupon_amount_cents=order.coupon_amount_cents,
        points_used=order.points_used,
        expires_at=order.expires_at,
        paid_at=order.paid_at,
        balance_after_wen=order.balance_after_wen,
        current_balance_wen=order.balance_after_wen,
        username=username,
        enterprise_name=enterprise_name,
    )


@router.get("/wallet", response_model=WalletResponse)
async def get_wallet(db: DBSession, current_user: CurrentUser) -> WalletResponse:
    wallet = await ensure_wallet(db, current_user.id)
    return WalletResponse(balance_wen=wallet.balance_wen, points=wallet.points)


@router.get("/packages", response_model=list[PackageResponse])
async def get_packages() -> list[PackageResponse]:
    return list_packages()


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
    # 内部用户看全站（JOIN users 取归属）；外部用户只看自己的（归属恒为本人，但同样 JOIN 以统一返回结构）
    stmt = select(BillingOrder, User.username, User.enterprise_name).join(
        User, User.id == BillingOrder.user_id, isouter=True
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
    stmt = stmt.order_by(BillingOrder.created_at.desc())

    rows = (await db.execute(stmt)).all()
    return OrderListResponse(
        orders=[
            _order_response(order, username=u_name, enterprise_name=ent_name)
            for order, u_name, ent_name in rows
        ]
    )


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

    # 真实交行支付：订单仍 pending 时主动查交行网关，SUCCESS 即出账加文；过期则置 cancelled
    if (
        order.status == "pending"
        and order.external_order_no
    ):
        if order.expires_at < utc_now():
            order.status = "cancelled"
            await db.flush()
        elif await operate_recharge.query_order_status(order.external_order_no) == "success":
            wallet_result = await db.execute(
                select(UserWallet).where(UserWallet.user_id == current_user.id).with_for_update()
            )
            wallet = wallet_result.scalar_one_or_none()
            if wallet is None:
                wallet = await ensure_wallet(db, current_user.id)
            await complete_order(db, current_user, order, wallet=wallet)
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
    return ConsumptionListResponse(
        consumptions=[
            ConsumptionResponse(
                id=row.id,
                consumed_at=row.created_at,
                project_name=row.project_name,
                consumed_wen=row.consumed_wen,
                earned_points=row.earned_points,
                used_by=row.used_by,
                cost_cny=float(row.cost_cny) if row.cost_cny is not None else None,
                username=u_name,
                enterprise_name=ent_name,
            )
            for row, u_name, ent_name in rows
        ]
    )
