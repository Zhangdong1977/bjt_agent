"""Billing domain services."""

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models import (
    AiUsageTaskSummary,
    BillingOrder,
    ConsumptionRecord,
    Project,
    ReviewTask,
    User,
    UserWallet,
    WalletTransaction,
    async_session_factory,
)
from backend.schemas.billing import OrderPreviewResponse, PackageResponse
from backend.services.operate_coupons import find_available_coupon, mark_coupon_used
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


PACKAGES: dict[str, RechargePackage] = {
    # 测试套餐：1 分钱 / 200 文，仍走真实交行；prod 由 billing_hidden_package_codes 隐藏
    "test": RechargePackage("test", "测试套餐", 1, 200, "真实交行支付·0.01元"),
    "experience": RechargePackage("experience", "体验套餐", 3000, 350, "500页以上标书谨慎使用"),
    "basic": RechargePackage("basic", "基础套餐", 10000, 1200),
    "premium": RechargePackage("premium", "尊享套餐", 30000, 4000),
    "luxury": RechargePackage("luxury", "豪华套餐", 100000, 15000),
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
        )
        for item in PACKAGES.values()
        if _is_package_visible(item.code, settings)
    ]


def get_package(package_code: str) -> RechargePackage:
    package = PACKAGES.get(package_code)
    if not package:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="套餐不存在")
    return package


async def ensure_wallet(db: AsyncSession, user_id: str) -> UserWallet:
    result = await db.execute(select(UserWallet).where(UserWallet.user_id == user_id))
    wallet = result.scalar_one_or_none()
    if wallet:
        return wallet
    wallet = UserWallet(user_id=user_id, balance_wen=0, points=0)
    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)
    return wallet


def cost_to_wen(cost_cny: Decimal | float | str | None) -> int:
    if cost_cny is None:
        return 0
    cost = Decimal(str(cost_cny))
    if cost <= 0:
        return 0
    return int((cost * Decimal("40")).to_integral_value(rounding=ROUND_CEILING))


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
    package = get_package(package_code)
    # 安全：与 list_packages() 用同一个 _is_package_visible 闸门。
    # 否则隐藏的 test 套餐（1 分钱 / 200 文）能被知道 code 的用户绕过前端直接 preview / 下单。
    if not _is_package_visible(package.code, get_settings()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="套餐不存在")
    wallet = await ensure_wallet(db, current_user.id)

    coupon_amount_cents = 0
    if coupon_id is not None:
        coupon = await find_available_coupon(current_user.username, coupon_id)
        if not coupon:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="优惠券不可用")
        coupon_amount_cents = min(coupon.amount_cents, package.amount_cents)

    remaining_after_coupon = max(0, package.amount_cents - coupon_amount_cents)
    max_points_by_amount = remaining_after_coupon // POINT_CENT_VALUE
    requested_points = max(0, use_points)
    points_used = min(requested_points, wallet.points, max_points_by_amount)
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
        package_balance_wen=package.balance_wen,
        current_balance_wen=wallet.balance_wen,
        current_points=wallet.points,
    )


async def create_order(
    db: AsyncSession,
    current_user: User,
    *,
    package_code: str,
    coupon_id: int | None,
    use_points: int,
) -> BillingOrder:
    package = get_package(package_code)
    # 安全：纵深防御——preview_order 内也有同样校验，但这里提前拦截避免 ensure_wallet 副作用。
    if not _is_package_visible(package.code, get_settings()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="套餐不存在")
    wallet = await ensure_wallet(db, current_user.id)
    preview = await preview_order(
        db,
        current_user,
        package_code=package_code,
        coupon_id=coupon_id,
        use_points=use_points,
    )

    coupon_code = None
    if coupon_id is not None:
        coupon = await find_available_coupon(current_user.username, coupon_id)
        if not coupon:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="优惠券不可用")
        coupon_code = coupon.code

    order = BillingOrder(
        order_no=generate_order_no(),
        user_id=current_user.id,
        product_code=package.code,
        product_name=package.name,
        status="pending",
        order_amount_cents=preview.order_amount_cents,
        actual_payment_cents=preview.actual_payment_cents,
        package_balance_wen=package.balance_wen,
        coupon_id=coupon_id,
        coupon_code=coupon_code,
        coupon_amount_cents=preview.coupon_amount_cents,
        points_used=preview.points_used,
        points_amount_cents=preview.points_amount_cents,
        expires_at=utc_now() + timedelta(minutes=30),
    )
    db.add(order)
    await db.flush()

    if order.actual_payment_cents == 0:
        await complete_order(db, current_user, order, wallet=wallet)
    return order


async def complete_order(
    db: AsyncSession,
    current_user: User,
    order: BillingOrder,
    *,
    wallet: UserWallet | None = None,
    allow_expired_if_paid: bool = False,
) -> BillingOrder:
    """Complete a recharge order: deduct points, add wen, write wallet transaction.

    Args:
        allow_expired_if_paid: 设为 True 时跳过 expires_at 过期校验。
            场景：交行真实付款回调晚于订单 30 分钟过期（用户离开页面、回调未及时接收等），
            定时任务扫到交行 SUCCESS 后必须强制入账——钱已收就必须给文，否则吞钱。
            默认 False 保持 API 端点（get_order_status 等）的原有行为：过期即拒绝。
    """
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
        )
        wallet = result.scalar_one_or_none()
        if wallet is None:
            wallet = await ensure_wallet(db, current_user.id)

    if wallet.points < order.points_used:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="积分余额不足")

    wallet.points -= order.points_used
    wallet.balance_wen += order.package_balance_wen
    order.status = "completed"
    order.paid_at = utc_now()
    order.balance_after_wen = wallet.balance_wen

    db.add(
        WalletTransaction(
            user_id=current_user.id,
            transaction_type="recharge",
            balance_delta_wen=order.package_balance_wen,
            balance_after_wen=wallet.balance_wen,
            points_delta=-order.points_used,
            points_after=wallet.points,
            reference_type="order",
            reference_id=order.id,
            description=f"{order.product_name}充值",
        )
    )
    await db.flush()

    if order.coupon_id is not None:
        marked = await mark_coupon_used(order.coupon_id)
        if not marked:
            logger.warning("[billing] coupon %s was not marked used for order %s", order.coupon_id, order.order_no)

    return order


async def settle_review_consumption(task_id: str) -> ConsumptionRecord | None:
    """Settle a completed review task exactly once.

    Billing rule: consumed wen = ceil(cost_cny * 4 * 10) = ceil(cost_cny * 40).
    If the balance is insufficient, the wallet may become negative so the
    already completed AI review is still represented honestly in the ledger.
    """
    async with async_session_factory() as db:
        existing = await db.execute(select(ConsumptionRecord).where(ConsumptionRecord.task_id == task_id))
        if existing.scalar_one_or_none():
            return None

        task_result = await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
        task = task_result.scalar_one_or_none()
        if not task or task.status != "completed":
            return None

        project_result = await db.execute(select(Project).where(Project.id == task.project_id))
        project = project_result.scalar_one_or_none()
        if not project:
            return None

        summary_result = await db.execute(select(AiUsageTaskSummary).where(AiUsageTaskSummary.id == task_id))
        summary = summary_result.scalar_one_or_none()

        user_result = await db.execute(select(User).where(User.id == project.user_id))
        user = user_result.scalar_one_or_none()
        # 内部用户与外部用户走统一计费流程，不再豁免（便于内部测试计费/积分）。

        consumed_wen = cost_to_wen(summary.cost_cny if summary else None)

        wallet_result = await db.execute(
            select(UserWallet)
            .where(UserWallet.user_id == project.user_id)
            .with_for_update()
        )
        wallet = wallet_result.scalar_one_or_none()
        if wallet is None:
            wallet = UserWallet(user_id=project.user_id, balance_wen=0, points=0)
            db.add(wallet)
            await db.flush()

        wallet.balance_wen -= consumed_wen
        earned_points = consumed_wen  # 每消费 1 文返 1 积分
        wallet.points += earned_points

        used_by = user.username if user else project.user_id

        record = ConsumptionRecord(
            user_id=project.user_id,
            task_id=task_id,
            project_id=project.id,
            project_name=project.name,
            consumed_wen=consumed_wen,
            earned_points=earned_points,
            used_by=used_by,
            cost_cny=summary.cost_cny if summary else None,
            balance_after_wen=wallet.balance_wen,
        )
        db.add(record)
        db.add(
            WalletTransaction(
                user_id=project.user_id,
                transaction_type="ai_check",
                balance_delta_wen=-consumed_wen,
                balance_after_wen=wallet.balance_wen,
                points_delta=earned_points,
                points_after=wallet.points,
                reference_type="review_task",
                reference_id=task_id,
                description=f"{project.name} AI检查",
            )
        )
        await db.commit()
        logger.info("[billing] settled task %s: cost=%s, wen=%s", task_id, record.cost_cny, consumed_wen)
        return record
