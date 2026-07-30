"""Celery tasks for billing: pending recharge order polling.

定时扫所有 pending 状态的真实交行订单，主动调交行查单接口同步状态：
- SUCCESS → complete_order(allow_expired_if_paid=True) 入账（钱已收就必须给点）
- failure → status='cancelled'，日志告警
- pending → expires_at 过期则置 cancelled

为什么需要这个任务：
- 交行异步 notify 打到 operate-two，但 bjt-agent 链路不接收（设计如此）。
- API 端点 get_order_status 是被动触发（用户在前端点"我已支付"才会查交行）。
  若用户离开页面、网络异常或回调晚到，订单会永远停在 pending——用户付了钱却拿不到点。
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


async def _run_with_global_engine(coro):
    """Clear loop-bound pooled connections around a Celery asyncio.run call."""
    from backend.models import engine

    await engine.dispose()
    try:
        return await coro
    finally:
        await engine.dispose()


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
    from backend.models import BillingOrder, User
    from backend.services import operate_recharge
    from backend.services.billing import complete_order
    from backend.services.operate_coupons import release_coupon
    from backend.utils.time_utils import utc_now

    processed = {"completed": 0, "cancelled": 0, "skipped": 0, "errors": 0}
    cutoff = utc_now() - timedelta(hours=24)

    async with session_factory() as db:
        result = await db.execute(
            select(BillingOrder.id).where(
                BillingOrder.status == "pending",
                BillingOrder.external_order_no.is_not(None),
                BillingOrder.created_at >= cutoff,
            )
        )
        order_ids = result.scalars().all()

        if not order_ids:
            return processed

        for order_id in order_ids:
            order = (
                await db.execute(select(BillingOrder).where(BillingOrder.id == order_id))
            ).scalar_one_or_none()
            if order is None or order.status != "pending":
                processed["skipped"] += 1
                await db.rollback()
                continue
            order_no = order.order_no
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
                        await db.rollback()
                        continue

                    was_expired = order.expires_at < utc_now()
                    await complete_order(
                        db,
                        user,
                        order,
                        allow_expired_if_paid=True,  # 交行收了钱就必须给点
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
                    if order.coupon_id is not None:
                        await release_coupon(order.coupon_id, order.order_no)
                    await db.flush()
                    processed["cancelled"] += 1
                    logger.warning(
                        "[billing-poll] order %s cancelled (交行 failure)", order.order_no
                    )

                else:  # pending
                    # 交行还没收到付款，订单过期则置 cancelled（清理未付过期单）
                    if order.expires_at < utc_now():
                        order.status = "cancelled"
                        if order.coupon_id is not None:
                            await release_coupon(order.coupon_id, order.order_no)
                        await db.flush()
                        processed["cancelled"] += 1
                    else:
                        processed["skipped"] += 1

                # Commit each order independently. One database or network
                # failure must not poison the session for the remaining batch,
                # and row locks should not be held across later gateway calls.
                await db.commit()

            except Exception as e:
                processed["errors"] += 1
                await db.rollback()
                # 单条出错不影响其它订单；下一轮 beat 再扫这条
                logger.exception(
                    "[billing-poll] order %s sync failed: %s", order_no, e
                )

    logger.info(
        "[billing-poll] scanned %d orders: %s", len(order_ids), processed
    )
    return processed


@celery_app.task(bind=True, name="backend.tasks.billing_tasks.expire_credit_lots")
def expire_credit_lots(self) -> dict:
    """Expire independently dated recharge/gift lots and update wallet totals."""
    return asyncio.run(_run_with_session(_expire_credit_lots_async))


async def _expire_credit_lots_async(session_factory) -> dict:
    from backend.services.sales import expire_all_due_lots

    totals = {"users": 0, "lots": 0}
    # Drain bounded batches. A normal hourly run should complete in one pass;
    # the loop also handles a large first cutover without one unbounded query.
    while True:
        async with session_factory() as db:
            batch = await expire_all_due_lots(db, user_limit=500)
            await db.commit()
        totals["users"] += batch["users"]
        totals["lots"] += batch["lots"]
        if batch["users"] < 500:
            break
    logger.info("[billing-expiry] expired lots: %s", totals)
    return totals


@celery_app.task(
    bind=True,
    name="backend.tasks.billing_tasks.settle_task_billing",
    max_retries=8,
)
def settle_task_billing(self, task_kind: str, task_id: str) -> dict:
    """Reliably settle one terminal task; retry and reconciliation are backups."""
    from backend.services.billing import settle_task_consumption

    try:
        record = asyncio.run(
            _run_with_global_engine(settle_task_consumption(task_kind, task_id))
        )
        return {"settled": record is not None, "task_kind": task_kind, "task_id": task_id}
    except Exception as exc:
        countdown = min(300, 2 ** min(self.request.retries + 2, 8))
        raise self.retry(exc=exc, countdown=countdown)


@celery_app.task(bind=True, name="backend.tasks.billing_tasks.dispatch_pending_task_outbox")
def dispatch_pending_task_outbox(self) -> dict:
    from backend.services.task_lifecycle import dispatch_pending_task_outbox as dispatch

    return asyncio.run(_run_with_global_engine(dispatch(limit=100)))


@celery_app.task(bind=True, name="backend.tasks.billing_tasks.reconcile_task_billing")
def reconcile_task_billing(self) -> dict:
    return asyncio.run(_run_with_global_engine(_reconcile_task_billing_async()))


async def _reconcile_task_billing_async() -> dict:
    """Recover orphan terminal tasks and retry all durable unsettled tasks."""
    from backend.config import get_settings
    from backend.models import (
        BlindCheckTask,
        ReviewTask,
        TaskDispatchOutbox,
        async_session_factory,
    )
    from backend.services.billing import settle_task_consumption
    from backend.services.usage_summary import refresh_task_summary
    from backend.utils.time_utils import utc_now

    cutoff = utc_now() - timedelta(
        seconds=max(60, get_settings().billing_orphan_finalize_grace_seconds)
    )
    candidates: list[tuple[str, str]] = []
    orphaned: list[tuple[str, str]] = []
    async with async_session_factory() as db:
        # Broker acknowledgement without later worker claim is recoverable by
        # re-opening the outbox row. Re-delivery uses the same Celery id and the
        # database claim lock prevents duplicate provider work.
        stale_dispatches = list(
            (
                await db.execute(
                    select(TaskDispatchOutbox).where(
                        TaskDispatchOutbox.status == "dispatched",
                        TaskDispatchOutbox.dispatched_at <= cutoff,
                    ).limit(200)
                )
            ).scalars()
        )
        for outbox in stale_dispatches:
            model = BlindCheckTask if outbox.task_kind == "blind_check" else ReviewTask
            task = (
                await db.execute(select(model).where(model.id == outbox.task_id))
            ).scalar_one_or_none()
            waiting_statuses = (
                {"created", "waiting_for_document"}
                if outbox.task_kind == "blind_check"
                else {"pending"}
            )
            if (
                task is not None
                and task.status in waiting_statuses
                and task.billing_status != "legacy"
            ):
                outbox.status = "retry"
                outbox.next_attempt_at = None
                outbox.last_error = "worker claim timeout; redispatching"

        # Celery hard-kill/process-loss can bypass worker finally blocks.  The
        # absolute task runtime is a deterministic upper bound; once exceeded,
        # move the business task to failed so cost reconciliation can proceed.
        review_runtime_cutoff = utc_now() - timedelta(
            seconds=get_settings().agent_total_timeout
            + get_settings().billing_orphan_finalize_grace_seconds
        )
        stuck_reviews = list(
            (
                await db.execute(
                    select(ReviewTask).where(
                        ReviewTask.status == "running",
                        ReviewTask.billing_status != "legacy",
                        ReviewTask.started_at <= review_runtime_cutoff,
                    )
                )
            ).scalars()
        )
        blind_runtime_cutoff = utc_now() - timedelta(
            seconds=25 * 60 + get_settings().billing_orphan_finalize_grace_seconds
        )
        stuck_blind = list(
            (
                await db.execute(
                    select(BlindCheckTask).where(
                        BlindCheckTask.status == "running",
                        BlindCheckTask.billing_status != "legacy",
                        BlindCheckTask.started_at <= blind_runtime_cutoff,
                    )
                )
            ).scalars()
        )
        for task in [*stuck_reviews, *stuck_blind]:
            task.status = "failed"
            task.error_message = "任务执行进程异常退出或超过最长运行时间，系统已自动结束"
            task.completed_at = utc_now()
            task.billing_status = "pending"
        if stuck_reviews or stuck_blind:
            await db.flush()

        review_rows = list(
            (
                await db.execute(
                    select(ReviewTask).where(
                        ReviewTask.status.in_(("completed", "failed", "cancelled")),
                        ReviewTask.billing_status.in_(("pending", "retry", "processing")),
                    ).limit(200)
                )
            ).scalars()
        )
        blind_rows = list(
            (
                await db.execute(
                    select(BlindCheckTask).where(
                        BlindCheckTask.status.in_(("completed", "failed", "cancelled")),
                        BlindCheckTask.billing_status.in_(("pending", "retry", "processing")),
                    ).limit(200)
                )
            ).scalars()
        )
        for task in review_rows:
            kind = task.task_type
            if task.usage_finalized_at is not None:
                candidates.append((kind, task.id))
            elif (
                (
                    task.billing_status == "pending"
                    or (
                        task.billing_status == "retry"
                        and not (task.billing_error or "").startswith("USAGE_WRITE_FAILED:")
                    )
                )
                and task.completed_at is not None
                and task.completed_at <= cutoff
            ):
                orphaned.append((kind, task.id))
        for task in blind_rows:
            if task.usage_finalized_at is not None:
                candidates.append(("blind_check", task.id))
            elif (
                (
                    task.billing_status == "pending"
                    or (
                        task.billing_status == "retry"
                        and not (task.billing_error or "").startswith("USAGE_WRITE_FAILED:")
                    )
                )
                and task.completed_at is not None
                and task.completed_at <= cutoff
            ):
                orphaned.append(("blind_check", task.id))
        await db.commit()

    # A terminal task left pending past the grace period has no live worker.
    # Rebuild its summary from durable rows, then mark the usage gate complete.
    for kind, task_id in orphaned:
        await refresh_task_summary(task_id, strict=True)
        model = BlindCheckTask if kind == "blind_check" else ReviewTask
        async with async_session_factory() as db:
            task = (
                await db.execute(select(model).where(model.id == task_id).with_for_update())
            ).scalar_one_or_none()
            if (
                task
                and task.billing_status in {"pending", "retry"}
                and not (task.billing_error or "").startswith("USAGE_WRITE_FAILED:")
                and task.usage_finalized_at is None
            ):
                task.usage_finalized_at = utc_now()
                task.billing_status = "pending"
                task.billing_error = None
                await db.commit()
                candidates.append((kind, task_id))

    result = {"candidates": len(candidates), "settled": 0, "errors": 0, "orphaned": len(orphaned)}
    for kind, task_id in dict.fromkeys(candidates):
        try:
            await settle_task_consumption(kind, task_id)
            result["settled"] += 1
        except Exception:
            result["errors"] += 1
            logger.exception("[billing-reconcile] failed: kind=%s task=%s", kind, task_id)
    return result
