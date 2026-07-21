"""Celery tasks for billing: pending recharge order polling.

定时扫所有 pending 状态的真实交行订单，主动调交行查单接口同步状态：
- SUCCESS → complete_order(allow_expired_if_paid=True) 入账（钱已收就必须给文）
- failure → status='cancelled'，日志告警
- pending → expires_at 过期则置 cancelled

为什么需要这个任务：
- 交行异步 notify 打到 operate-two，但 bjt-agent 链路不接收（设计如此）。
- API 端点 get_order_status 是被动触发（用户在前端点"我已支付"才会查交行）。
  若用户离开页面、网络异常或回调晚到，订单会永远停在 pending——用户付了钱却拿不到文。
- 这个 beat 任务是兜底：不依赖用户在线，每 60s 主动扫一次。

事故触发：订单 BJT202607210252559C0165（basic 90 元）用户付款成功但前端轮询已停止，
订单过期后永远 pending，钱包未入账。手工补单后加此任务根除同类问题。
"""

import asyncio
import logging
from datetime import timedelta

from backend.celery_app import celery_app
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def _run_with_session(coro_factory):
    """Run an async function with a fresh engine/session, disposing afterwards.

    Celery prefork workers call asyncio.run() which creates a new event loop
    per invocation. The module-level async_session_factory from base.py has a
    connection pool bound to the *original* loop — reusing it causes
    "Event loop is closed" errors. Instead we create a task-scoped engine
    and dispose it when done.（同 feedback_tasks / experience_tasks 实现方式）
    """
    from backend.tasks.review_tasks import create_session_factory

    session_factory, engine = create_session_factory()
    try:
        return await coro_factory(session_factory)
    finally:
        await engine.dispose()


@celery_app.task(bind=True, name="backend.tasks.billing_tasks.poll_pending_recharge_orders")
def poll_pending_recharge_orders(self) -> dict:
    """扫所有 pending 真实交行订单，调交行查单同步状态。每 60s 由 beat 派发一次。"""
    return asyncio.run(_run_with_session(_poll_async))


async def _poll_async(session_factory) -> dict:
    """单次扫描的异步实现。

    设计要点：
    - 只扫真实支付订单（external_order_no 非空）+ 近 24h 内创建（避免历史订单堆积）。
    - 单条订单异常不中断整批——一条出错不影响其它订单的入账。
    - complete_order 内部 status='completed' 提前返回 + with_for_update 钱包行锁，幂等可重入。
    """
    from backend.models import BillingOrder, User, UserWallet
    from backend.services import operate_recharge
    from backend.services.billing import complete_order, ensure_wallet
    from backend.utils.time_utils import utc_now

    processed = {"completed": 0, "cancelled": 0, "skipped": 0, "errors": 0}
    cutoff = utc_now() - timedelta(hours=24)

    async with session_factory() as db:
        result = await db.execute(
            select(BillingOrder).where(
                BillingOrder.status == "pending",
                BillingOrder.external_order_no.is_not(None),
                BillingOrder.created_at >= cutoff,
            )
        )
        orders = result.scalars().all()

        if not orders:
            return processed

        for order in orders:
            try:
                pay_status = await operate_recharge.query_order_status(order.external_order_no)

                if pay_status == "success":
                    # 拉订单归属 user（complete_order 校验 user_id 一致）
                    user_result = await db.execute(
                        select(User).where(User.id == order.user_id)
                    )
                    user = user_result.scalar_one_or_none()
                    if user is None:
                        logger.warning(
                            "[billing-poll] order %s: user %s not found, skip",
                            order.order_no, order.user_id,
                        )
                        processed["skipped"] += 1
                        continue

                    wallet_result = await db.execute(
                        select(UserWallet)
                        .where(UserWallet.user_id == order.user_id)
                        .with_for_update()
                    )
                    wallet = wallet_result.scalar_one_or_none()
                    if wallet is None:
                        wallet = await ensure_wallet(db, order.user_id)

                    was_expired = order.expires_at < utc_now()
                    await complete_order(
                        db,
                        user,
                        order,
                        wallet=wallet,
                        allow_expired_if_paid=True,  # 交行收了钱就必须给文
                    )
                    await db.flush()
                    processed["completed"] += 1
                    logger.info(
                        "[billing-poll] order %s completed (paid via 交行 success%s)",
                        order.order_no,
                        ", was expired—补单" if was_expired else "",
                    )

                elif pay_status == "failure":
                    order.status = "cancelled"
                    await db.flush()
                    processed["cancelled"] += 1
                    logger.warning(
                        "[billing-poll] order %s cancelled (交行 failure)", order.order_no
                    )

                else:  # pending
                    # 交行还没收到付款，订单过期则置 cancelled（清理未付过期单）
                    if order.expires_at < utc_now():
                        order.status = "cancelled"
                        await db.flush()
                        processed["cancelled"] += 1
                    else:
                        processed["skipped"] += 1

            except Exception as e:
                processed["errors"] += 1
                # 单条出错不影响其它订单；下一轮 beat 再扫这条
                logger.exception(
                    "[billing-poll] order %s sync failed: %s", order.order_no, e
                )

        await db.commit()

    logger.info(
        "[billing-poll] scanned %d orders: %s", len(orders), processed
    )
    return processed
