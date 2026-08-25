"""Billing domain services."""

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models import (
    AiUsageRecord,
    BillingOrder,
    BlindCheckTask,
    ConsumptionRecord,
    Project,
    ReviewTask,
    TASK_MODEL_BY_KIND,
    User,
    UserWallet,
    WalletTransaction,
    SalesPackage,
    async_session_factory,
)
from backend.schemas.billing import OrderPreviewResponse, PackageResponse
from backend.services.operate_coupons import consume_coupon, release_coupon, reserve_coupon, validate_coupon
from backend.services.sales import (
    MONEY_QUANT,
    add_credit_lot,
    add_months,
    allocate_consumption,
    decimal_value,
    get_sales_config,
    get_sales_package,
    list_sales_packages,
    point_value,
    sync_legacy_balance,
)
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

POINT_CENT_VALUE = 1  # 1 point = 1 cent = 0.01 CNY（充值时 1 积分抵 1 分钱）


@dataclass(frozen=True)
class RechargePackage:
    code: str
    name: str
    amount_cents: int
    balance_wen: int
    caution: str | None = None
    recharge_points: Decimal = Decimal("0")
    gift_points: Decimal = Decimal("0")
    validity_months: int = 12
    loyalty_deduction_limit: int | None = None
    icon_url: str | None = None


PACKAGES: dict[str, RechargePackage] = {
    # 测试套餐：1 分钱 / 200 点，仍走真实交行；prod 由 billing_hidden_package_codes 隐藏
    "test": RechargePackage("test", "测试套餐", 1, 200, "真实交行支付·0.01元"),
    "experience": RechargePackage("experience", "体验套餐", 3000, 350, "500页以上标书谨慎使用", Decimal("300"), Decimal("50")),
    "basic": RechargePackage("basic", "基础套餐", 10000, 1200, None, Decimal("1000"), Decimal("200")),
    "premium": RechargePackage("premium", "尊享套餐", 30000, 4000, None, Decimal("3000"), Decimal("1000")),
    "luxury": RechargePackage("luxury", "豪华套餐", 100000, 15000, None, Decimal("10000"), Decimal("5000")),
}


def _parse_codes(value: str) -> set[str]:
    return {part.strip() for part in value.split(",") if part.strip()}


def _is_package_visible(code: str, settings) -> bool:
    """套餐是否对终端用户可见。

    两道闸门，任一命中即隐藏：
    1. fail-closed：测试套餐（code="test"）默认隐藏，仅在 billing_test_package_enabled=true 时显示。
       这样即使漏配 BILLING_HIDDEN_PACKAGE_CODES，prod 也不会暴露 1 分钱测试套餐。
    2. 显式隐藏清单 billing_hidden_package_codes（逗号分隔），用于运维按需下架任意套餐。
    """
    if code == "test" and not settings.billing_test_package_enabled:
        return False
    if code in _parse_codes(settings.billing_hidden_package_codes):
        return False
    return True


def list_packages() -> list[PackageResponse]:
    settings = get_settings()
    return [
        PackageResponse(
            code=item.code,
            name=item.name,
            amount_cents=item.amount_cents,
            balance_wen=item.balance_wen,
            caution=item.caution,
            recharge_points=float(item.recharge_points or item.balance_wen),
            gift_points=float(item.gift_points),
            total_points=float(item.recharge_points + item.gift_points) if item.recharge_points else float(item.balance_wen),
            validity_months=item.validity_months,
            loyalty_deduction_limit=item.loyalty_deduction_limit,
            icon_url=item.icon_url,
        )
        for item in PACKAGES.values()
        if _is_package_visible(item.code, settings)
    ]


def get_package(package_code: str) -> RechargePackage:
    package = PACKAGES.get(package_code)
    if not package:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="套餐不存在")
    return package


async def ensure_wallet(db: AsyncSession, user_id: str, *, for_update: bool = False) -> UserWallet:
    stmt = select(UserWallet).where(UserWallet.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    result = await db.execute(stmt)
    wallet = result.scalar_one_or_none()
    if wallet:
        # Expand/contract compatibility: before migration legacy rows only have
        # balance_wen. Conservatively classify that balance as recharge points.
        if (
            decimal_value(wallet.recharge_balance_points) == 0
            and decimal_value(wallet.gift_balance_points) == 0
            and wallet.balance_wen != 0
        ):
            wallet.recharge_balance_points = point_value(wallet.balance_wen)
        return wallet
    wallet = UserWallet(
        user_id=user_id,
        balance_wen=0,
        points=0,
        recharge_balance_points=Decimal("0"),
        gift_balance_points=Decimal("0"),
    )
    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)
    return wallet


def cost_to_points(cost_cny: Decimal | float | str | None) -> Decimal:
    if cost_cny is None:
        return Decimal("0.000000")
    cost = Decimal(str(cost_cny))
    if cost <= 0:
        return Decimal("0.000000")
    # Cost comes from the six-decimal AI usage ledger. Preserve that precision
    # until the final sales-points calculation; only sales points are rounded
    # to two decimals by the product rule.
    return (cost * Decimal("10")).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def sales_points_for(cost_cny: Decimal | float | str | None, multiplier: Decimal | float | str) -> Decimal:
    return point_value(cost_to_points(cost_cny) * Decimal(str(multiplier)))


def cost_to_wen(cost_cny: Decimal | float | str | None) -> int:
    """Legacy alias using the default 4x multiplier, rounded half-up."""
    return int(sales_points_for(cost_cny, Decimal("4")).to_integral_value(rounding=ROUND_HALF_UP))


async def list_runtime_packages(db: AsyncSession) -> list[PackageResponse]:
    rows = await list_sales_packages(db, online_only=True)
    settings = get_settings()
    return [_package_response(row) for row in rows if _is_package_visible(row.code, settings)]


async def get_runtime_package(db: AsyncSession, package_code: str) -> SalesPackage:
    settings = get_settings()
    if not _is_package_visible(package_code, settings):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="套餐不存在或已下线")
    return await get_sales_package(db, package_code, require_online=True)


def _package_response(item: SalesPackage) -> PackageResponse:
    recharge = point_value(item.recharge_points)
    gift = point_value(item.gift_points)
    total = recharge + gift
    return PackageResponse(
        code=item.code,
        name=item.name,
        amount_cents=item.amount_cents,
        balance_wen=int(total.to_integral_value(rounding=ROUND_HALF_UP)),
        caution=item.caution,
        icon_url=item.icon_url,
        recharge_points=float(recharge),
        gift_points=float(gift),
        total_points=float(total),
        validity_months=item.validity_months,
        loyalty_deduction_limit=item.loyalty_deduction_limit,
    )


def generate_order_no() -> str:
    return f"BJT{utc_now().strftime('%Y%m%d%H%M%S')}{secrets.token_hex(3).upper()}"


async def preview_order(
    db: AsyncSession,
    current_user: User,
    *,
    package_code: str,
    coupon_id: int | None,
    use_points: int,
) -> OrderPreviewResponse:
    package = await get_runtime_package(db, package_code)
    wallet = await ensure_wallet(db, current_user.id)

    coupon_amount_cents = 0
    coupon_benefit_type = None
    coupon_gift_points = Decimal("0")
    if coupon_id is not None:
        coupon = await validate_coupon(
            current_user.username,
            coupon_id,
            order_amount_cents=package.amount_cents,
        )
        coupon_benefit_type = coupon.benefit_type
        if coupon.benefit_type == "gift":
            coupon_gift_points = point_value(coupon.gift_points)
        else:
            coupon_amount_cents = min(coupon.amount_cents, package.amount_cents)

    remaining_after_coupon = max(0, package.amount_cents - coupon_amount_cents)
    max_points_by_amount = remaining_after_coupon // POINT_CENT_VALUE
    requested_points = max(0, use_points)
    package_limit = package.loyalty_deduction_limit
    points_used = min(
        requested_points,
        wallet.points,
        max_points_by_amount,
        package_limit if package_limit is not None else max_points_by_amount,
    )
    points_amount_cents = points_used * POINT_CENT_VALUE
    actual_payment_cents = max(0, remaining_after_coupon - points_amount_cents)

    return OrderPreviewResponse(
        package_code=package.code,
        product_name=package.name,
        order_amount_cents=package.amount_cents,
        coupon_amount_cents=coupon_amount_cents,
        points_used=points_used,
        points_amount_cents=points_amount_cents,
        actual_payment_cents=actual_payment_cents,
        package_balance_wen=int(
            (decimal_value(package.recharge_points) + decimal_value(package.gift_points) + coupon_gift_points).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        ),
        current_balance_wen=wallet.balance_wen,
        current_points=wallet.points,
        recharge_points=float(package.recharge_points),
        gift_points=float(decimal_value(package.gift_points) + coupon_gift_points),
        total_points=float(decimal_value(package.recharge_points) + decimal_value(package.gift_points) + coupon_gift_points),
        validity_months=package.validity_months,
        loyalty_deduction_limit=package.loyalty_deduction_limit,
        current_recharge_points=float(wallet.recharge_balance_points),
        current_gift_points=float(wallet.gift_balance_points),
        coupon_benefit_type=coupon_benefit_type,
        coupon_gift_points=float(coupon_gift_points),
    )


async def create_order(
    db: AsyncSession,
    current_user: User,
    *,
    package_code: str,
    coupon_id: int | None,
    use_points: int,
) -> BillingOrder:
    package = await get_runtime_package(db, package_code)
    wallet = await ensure_wallet(db, current_user.id)
    preview = await preview_order(
        db,
        current_user,
        package_code=package_code,
        coupon_id=coupon_id,
        use_points=use_points,
    )

    coupon_code = None
    order_no = generate_order_no()
    if coupon_id is not None:
        coupon = await reserve_coupon(
            current_user.username,
            coupon_id,
            order_amount_cents=package.amount_cents,
            order_no=order_no,
        )
        coupon_code = coupon.code

    order = BillingOrder(
        order_no=order_no,
        user_id=current_user.id,
        product_code=package.code,
        product_name=package.name,
        status="pending",
        order_amount_cents=preview.order_amount_cents,
        actual_payment_cents=preview.actual_payment_cents,
        package_balance_wen=preview.package_balance_wen,
        recharge_points=point_value(package.recharge_points),
        gift_points=point_value(decimal_value(package.gift_points) + decimal_value(preview.coupon_gift_points)),
        total_points=point_value(decimal_value(package.recharge_points) + decimal_value(package.gift_points) + decimal_value(preview.coupon_gift_points)),
        validity_months=package.validity_months,
        recharge_balance_before=point_value(wallet.recharge_balance_points),
        gift_balance_before=point_value(wallet.gift_balance_points),
        coupon_id=coupon_id,
        coupon_code=coupon_code,
        coupon_amount_cents=preview.coupon_amount_cents,
        coupon_benefit_type=preview.coupon_benefit_type,
        coupon_gift_points=point_value(preview.coupon_gift_points),
        points_used=preview.points_used,
        points_amount_cents=preview.points_amount_cents,
        expires_at=utc_now() + timedelta(minutes=30),
    )
    try:
        db.add(order)
        await db.flush()
        if order.actual_payment_cents == 0:
            # complete_order owns the lock order: order row first, wallet row
            # second. Do not pass the previously read, unlocked wallet here.
            await complete_order(db, current_user, order)
    except Exception:
        if coupon_id is not None:
            try:
                await release_coupon(coupon_id, order_no)
            except Exception:
                logger.exception("[billing] failed to release coupon reservation for order %s", order_no)
        raise
    return order


async def complete_order(
    db: AsyncSession,
    current_user: User,
    order: BillingOrder,
    *,
    wallet: UserWallet | None = None,
    allow_expired_if_paid: bool = False,
) -> BillingOrder:
    """Complete a recharge order: deduct loyalty points, add balance units, write the wallet transaction.

    Args:
        allow_expired_if_paid: 设为 True 时跳过 expires_at 过期校验。
            场景：交行真实付款回调晚于订单 30 分钟过期（用户离开页面、回调未及时接收等），
            定时任务扫到交行 SUCCESS 后必须强制入账——钱已收就必须给点，否则吞钱。
            默认 False 保持 API 端点（get_order_status 等）的原有行为：过期即拒绝。
    """
    # Serialize every completion path (poller, status endpoint and zero-pay
    # checkout) on the order row. A wallet lock alone does not prevent two
    # requests that both loaded the order as pending from crediting twice.
    locked_order = (
        await db.execute(
            select(BillingOrder)
            .where(BillingOrder.id == order.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked_order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")
    order = locked_order
    if order.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在")
    if order.status == "completed":
        return order
    if order.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单状态不可支付")
    if order.expires_at < utc_now() and not allow_expired_if_paid:
        order.status = "cancelled"
        await db.flush()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单已过期")

    if wallet is None:
        result = await db.execute(
            select(UserWallet)
            .where(UserWallet.user_id == current_user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        wallet = result.scalar_one_or_none()
        if wallet is None:
            wallet = await ensure_wallet(db, current_user.id)

    if wallet.points < order.points_used:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="积分余额不足")

    if order.coupon_id is not None:
        try:
            await consume_coupon(order.coupon_id, order.order_no)
        except HTTPException:
            if not allow_expired_if_paid:
                raise
            # A genuinely paid, late callback must never be turned into a
            # swallowed payment because its 30-minute coupon reservation was
            # already released or reused. Preserve the recharge and leave an
            # ERROR audit trail for manual coupon reconciliation.
            logger.error(
                "[billing] paid expired order %s could not consume coupon %s; "
                "continuing credit to avoid swallowing payment",
                order.order_no,
                order.coupon_id,
                exc_info=True,
            )

    order.recharge_balance_before = point_value(wallet.recharge_balance_points)
    order.gift_balance_before = point_value(wallet.gift_balance_points)
    wallet.points -= order.points_used
    order.status = "completed"
    order.paid_at = utc_now()
    total_points = point_value(decimal_value(order.recharge_points) + decimal_value(order.gift_points))
    order.total_points = total_points
    order.unit_value_yuan = (
        (Decimal(order.actual_payment_cents) / Decimal("100") / total_points).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )
        if total_points > 0
        else Decimal("0")
    )
    expires_at = add_months(order.paid_at, order.validity_months)
    await add_credit_lot(
        db,
        wallet,
        lot_type="recharge",
        source_type="billing_order",
        source_id=order.id,
        points=point_value(order.recharge_points),
        unit_value_yuan=decimal_value(order.unit_value_yuan),
        expires_at=expires_at,
        external_user_id=current_user.external_user_id,
        description=f"{order.product_name}充值点数",
    )
    await add_credit_lot(
        db,
        wallet,
        lot_type="gift",
        source_type="billing_order",
        source_id=order.id,
        points=point_value(order.gift_points),
        unit_value_yuan=decimal_value(order.unit_value_yuan),
        expires_at=expires_at,
        external_user_id=current_user.external_user_id,
        description=f"{order.product_name}赠送点数",
    )
    sync_legacy_balance(wallet)
    order.recharge_balance_after = point_value(wallet.recharge_balance_points)
    order.gift_balance_after = point_value(wallet.gift_balance_points)
    order.balance_after_wen = wallet.balance_wen

    db.add(
        WalletTransaction(
            user_id=current_user.id,
            transaction_type="recharge",
            balance_delta_wen=int(total_points.to_integral_value(rounding=ROUND_HALF_UP)),
            balance_after_wen=wallet.balance_wen,
            points_delta=-order.points_used,
            points_after=wallet.points,
            reference_type="order",
            reference_id=order.id,
            description=f"{order.product_name}充值",
        )
    )
    await db.flush()

    return order


class BillingNotReady(RuntimeError):
    """The task is not yet safe to settle from its durable usage ledger."""


async def _mark_settlement_retry(task_kind: str, task_id: str, exc: Exception) -> None:
    model = TASK_MODEL_BY_KIND.get(task_kind, ReviewTask)
    async with async_session_factory() as db:
        task = (
            await db.execute(select(model).where(model.id == task_id).with_for_update())
        ).scalar_one_or_none()
        if task is None or task.billing_status in {"legacy", "settled"}:
            return
        task.billing_attempts += 1
        task.billing_status = "retry"
        task.billing_error = str(exc)[:2_000]
        await db.commit()


async def settle_task_consumption(task_kind: str, task_id: str) -> ConsumptionRecord | None:
    """Settle any terminal billable task exactly once from durable usage rows.

    Failed and cancelled tasks are intentionally billable: provider cost may
    have been incurred before the terminal business status was reached.
    """

    if task_kind not in {"review", "duplicate", "blind_check", "bid_draft", "polish"}:
        raise ValueError(f"unsupported task kind: {task_kind}")
    model = TASK_MODEL_BY_KIND.get(task_kind, ReviewTask)
    try:
        async with async_session_factory() as db:
            task = (
                await db.execute(select(model).where(model.id == task_id).with_for_update())
            ).scalar_one_or_none()
            if task is None:
                return None
            if isinstance(task, ReviewTask) and task.task_type != task_kind:
                return None
            if task.billing_status == "legacy":
                return None

            existing = (
                await db.execute(
                    select(ConsumptionRecord).where(ConsumptionRecord.task_id == task_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                task.billing_status = "settled"
                task.billing_error = None
                task.billing_settled_at = task.billing_settled_at or utc_now()
                await db.commit()
                return existing
            if task.status not in {"completed", "failed", "cancelled"}:
                return None
            if task.usage_finalized_at is None:
                raise BillingNotReady("任务用量尚未完成持久化，暂不结算")

            task.billing_status = "processing"
            task.billing_attempts += 1

            if task_kind == "blind_check":
                project = None
                user_id = task.user_id
                project_id = None
                project_name = task.document_name or "暗标检查"
            elif task_kind == "polish":
                project = None
                user_id = task.user_id
                project_id = None
                project_name = "AI 润色"
            else:
                project = (
                    await db.execute(select(Project).where(Project.id == task.project_id))
                ).scalar_one_or_none()
                if project is None:
                    raise BillingNotReady("计费任务关联项目不存在")
                user_id = project.user_id
                project_id = project.id
                project_name = project.name

            user = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
            config = await get_sales_config(db)
            # Use an explicit None/empty check instead of `or`: a multiplier of 0
            # (free feature) is falsy and would otherwise fall back to the global
            # multiplier, silently re-enabling billing on a free task.
            effective_multiplier = (
                task.billing_multiplier
                if task.billing_multiplier not in (None, "")
                else config.sales_multiplier
            )
            multiplier = decimal_value(effective_multiplier)
            cost_yuan = decimal_value(
                (
                    await db.execute(
                        select(func.coalesce(func.sum(AiUsageRecord.cost_cny), 0)).where(
                            AiUsageRecord.task_id == task_id
                        )
                    )
                ).scalar_one()
            )
            cost_points = cost_to_points(cost_yuan)
            sales_points = sales_points_for(cost_yuan, multiplier)

            wallet = (
                await db.execute(
                    select(UserWallet).where(UserWallet.user_id == user_id).with_for_update()
                )
            ).scalar_one_or_none()
            if wallet is None:
                wallet = UserWallet(
                    user_id=user_id,
                    balance_wen=0,
                    points=0,
                    recharge_balance_points=Decimal("0"),
                    gift_balance_points=Decimal("0"),
                )
                db.add(wallet)
                await db.flush()

            record = ConsumptionRecord(
                user_id=user_id,
                task_id=task_id,
                task_type=task_kind,
                task_status=task.status,
                project_id=project_id,
                project_name=project_name,
                consumed_wen=int(sales_points.to_integral_value(rounding=ROUND_HALF_UP)),
                earned_points=0,
                used_by=user.username if user else user_id,
                cost_cny=cost_yuan,
                balance_after_wen=wallet.balance_wen,
                cost_points=cost_points,
                sales_multiplier=multiplier,
                sales_points=sales_points,
            )
            db.add(record)
            await db.flush()
            allocation = await allocate_consumption(
                db,
                wallet,
                consumption_id=record.id,
                sales_points=sales_points,
                cost_yuan=cost_yuan,
                task_id=task_id,
                task_kind=task_kind,
            )
            income = decimal_value(allocation["folded_income"])
            profit = (income - cost_yuan).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            margin = (
                (profit / income).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
                if income > 0
                else None
            )
            record.earned_points = int(allocation["earned_loyalty"])
            record.gift_points_used = allocation["gift_used"]
            record.recharge_points_used = allocation["recharge_used"]
            record.recharge_balance_before = allocation["before_recharge"]
            record.gift_balance_before = allocation["before_gift"]
            record.recharge_balance_after = allocation["after_recharge"]
            record.gift_balance_after = allocation["after_gift"]
            record.weighted_unit_value_yuan = allocation["weighted_unit_value"]
            record.folded_income_yuan = income
            record.profit_yuan = profit
            record.profit_margin = margin
            record.balance_after_wen = wallet.balance_wen
            db.add(
                WalletTransaction(
                    user_id=user_id,
                    transaction_type="ai_check",
                    balance_delta_wen=-int(sales_points.to_integral_value(rounding=ROUND_HALF_UP)),
                    balance_after_wen=wallet.balance_wen,
                    points_delta=record.earned_points,
                    points_after=wallet.points,
                    reference_type=f"{task_kind}_task",
                    reference_id=task_id,
                    description=f"{project_name} AI检查",
                )
            )
            task.billing_status = "settled"
            task.billing_error = None
            task.billing_settled_at = utc_now()
            await db.commit()
            logger.info(
                "[billing] settled %s task %s: status=%s cost=%s multiplier=%s sales_points=%s%s",
                task_kind,
                task_id,
                task.status,
                cost_yuan,
                multiplier,
                sales_points,
                " [free-task] zero-multiplier" if multiplier == 0 else "",
            )
            return record
    except Exception as exc:
        await _mark_settlement_retry(task_kind, task_id, exc)
        raise


async def settle_review_consumption(task_id: str) -> ConsumptionRecord | None:
    """Compatibility wrapper for existing callers."""

    async with async_session_factory() as db:
        task_type = (
            await db.execute(select(ReviewTask.task_type).where(ReviewTask.id == task_id))
        ).scalar_one_or_none()
    if task_type not in {"review", "duplicate"}:
        return None
    return await settle_task_consumption(task_type, task_id)
