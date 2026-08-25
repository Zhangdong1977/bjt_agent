"""Reliable lifecycle helpers for billable top-level AI tasks."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models import (
    TASK_MODEL_BY_KIND,
    BidDraftTask,
    BlindCheckTask,
    PolishTask,
    Project,
    ReviewTask,
    TaskDispatchOutbox,
    async_session_factory,
)
from backend.services.billing import ensure_wallet
from backend.services.sales import decimal_value, expire_user_lots, get_sales_config
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ("completed", "failed", "cancelled")
UNSETTLED_BILLING_STATUSES = ("pending", "processing", "retry")

_TASK_NAMES = {
    "review": "backend.tasks.review_tasks.run_review",
    "duplicate": "backend.tasks.duplicate_tasks.run_duplicate_check",
    "blind_check": "backend.tasks.blind_check_tasks.run_blind_check",
    "bid_draft": "backend.tasks.bid_draft_tasks.run_bid_draft",
    "polish": "backend.tasks.polish_tasks.run_polish",
}

# Outbox delivery queue per kind. bid_draft runs on a dedicated "generation"
# queue so long generation runs cannot starve review/duplicate workers.
_TASK_QUEUES = {
    "review": "review",
    "duplicate": "review",
    "blind_check": "review",
    "polish": "review",
    "bid_draft": "generation",
}


async def authorize_billable_task_start(
    db: AsyncSession,
    *,
    user_id: str,
    operation_name: str,
):
    """Lock the wallet and validate balance plus account-level concurrency.

    The wallet row is the per-account serialization point, so concurrent API
    workers cannot both pass the active-task count and create paid work.
    """

    wallet = await ensure_wallet(db, user_id, for_update=True)
    await expire_user_lots(db, wallet)
    available = decimal_value(wallet.recharge_balance_points) + decimal_value(
        wallet.gift_balance_points
    )
    if available <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "INSUFFICIENT_BALANCE",
                "message": f"余额不足，请先充值后再发起{operation_name}",
                "balance_wen": wallet.balance_wen,
                "available_points": float(available),
            },
        )

    # Keep the account closed until the previous task is fully settled, not
    # merely until its business status becomes terminal.  Otherwise a billing
    # outage would let a user run tasks one-by-one against a stale positive
    # wallet and accumulate unbounded debt despite the active-task limit.
    review_count = (
        await db.execute(
            select(func.count(ReviewTask.id))
            .select_from(ReviewTask)
            .join(Project, Project.id == ReviewTask.project_id)
            .where(
                Project.user_id == user_id,
                ReviewTask.billing_status.in_(UNSETTLED_BILLING_STATUSES),
            )
        )
    ).scalar_one()
    blind_count = (
        await db.execute(
            select(func.count(BlindCheckTask.id)).where(
                BlindCheckTask.user_id == user_id,
                BlindCheckTask.billing_status.in_(UNSETTLED_BILLING_STATUSES),
            )
        )
    ).scalar_one()
    bid_draft_count = (
        await db.execute(
            select(func.count(BidDraftTask.id)).where(
                BidDraftTask.user_id == user_id,
                BidDraftTask.billing_status.in_(UNSETTLED_BILLING_STATUSES),
            )
        )
    ).scalar_one()
    polish_count = (
        await db.execute(
            select(func.count(PolishTask.id)).where(
                PolishTask.user_id == user_id,
                PolishTask.billing_status.in_(UNSETTLED_BILLING_STATUSES),
            )
        )
    ).scalar_one()
    unsettled_count = (
        int(review_count or 0)
        + int(blind_count or 0)
        + int(bid_draft_count or 0)
        + int(polish_count or 0)
    )
    limit = max(1, int(get_settings().billing_max_active_tasks_per_user))
    if unsettled_count >= limit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ACTIVE_BILLING_TASK_EXISTS",
                "message": "当前账号已有 AI 任务（检查/生成/润色）正在执行或结算，请等待完成后再重试",
                "active_tasks": unsettled_count,
                "max_active_tasks": limit,
            },
        )
    return await get_sales_config(db)


def add_task_dispatch(db: AsyncSession, *, task_kind: str, task_id: str) -> TaskDispatchOutbox:
    """Add an outbox row in the same transaction as the business task."""

    if task_kind not in _TASK_NAMES:
        raise ValueError(f"unsupported task kind: {task_kind}")
    celery_task_id = str(uuid.uuid4())
    row = TaskDispatchOutbox(
        task_kind=task_kind,
        task_id=task_id,
        celery_task_id=celery_task_id,
        status="pending",
    )
    db.add(row)
    return row


async def _dispatch_task_outbox(outbox_id: str) -> bool:
    """Deliver one outbox row, leaving it retryable on broker failure."""

    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(TaskDispatchOutbox)
                .where(TaskDispatchOutbox.id == outbox_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.status in {"dispatched", "cancelled"}:
            return False
        now = utc_now()
        if row.next_attempt_at and row.next_attempt_at > now:
            return False
        row.attempts += 1
        try:
            from backend.celery_app import celery_app

            celery_app.send_task(
                _TASK_NAMES[row.task_kind],
                args=[row.task_id],
                task_id=row.celery_task_id,
                queue=_TASK_QUEUES.get(row.task_kind, "review"),
            )
        except Exception as exc:
            row.status = "retry"
            row.last_error = str(exc)[:2_000]
            delay = min(300, 2 ** min(row.attempts, 8))
            row.next_attempt_at = now + timedelta(seconds=delay)
            await db.commit()
            logger.error(
                "[task-outbox] dispatch failed: kind=%s task=%s attempt=%s error=%s",
                row.task_kind,
                row.task_id,
                row.attempts,
                exc,
            )
            return False
        row.status = "dispatched"
        row.dispatched_at = now
        row.next_attempt_at = None
        row.last_error = None
        await db.commit()
        return True


async def dispatch_task_outbox(outbox_id: str) -> bool:
    """Fail-safe public dispatcher; the committed outbox remains retryable."""
    try:
        return await _dispatch_task_outbox(outbox_id)
    except Exception:
        logger.exception("[task-outbox] unexpected dispatch failure: outbox=%s", outbox_id)
        return False


async def dispatch_pending_task_outbox(*, limit: int = 50) -> dict[str, int]:
    now = utc_now()
    async with async_session_factory() as db:
        ids = list(
            (
                await db.execute(
                    select(TaskDispatchOutbox.id)
                    .where(
                        TaskDispatchOutbox.status.in_(("pending", "retry")),
                        or_(
                            TaskDispatchOutbox.next_attempt_at.is_(None),
                            TaskDispatchOutbox.next_attempt_at <= now,
                        ),
                    )
                    .order_by(TaskDispatchOutbox.created_at.asc())
                    .limit(limit)
                )
            ).scalars()
        )
    delivered = 0
    for outbox_id in ids:
        delivered += int(await dispatch_task_outbox(outbox_id))
    return {"selected": len(ids), "delivered": delivered}


async def cancel_pending_dispatch(db: AsyncSession, *, task_kind: str, task_id: str) -> bool:
    row = (
        await db.execute(
            select(TaskDispatchOutbox)
            .where(
                TaskDispatchOutbox.task_kind == task_kind,
                TaskDispatchOutbox.task_id == task_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.status == "dispatched":
        return False
    row.status = "cancelled"
    row.next_attempt_at = None
    row.last_error = "task cancelled before dispatch"
    return True


async def finalize_task_usage(task_kind: str, task_id: str) -> bool:
    """Flush local usage writes and mark a terminal task ready to settle.

    A strict summary refresh is part of the gate.  Any failure leaves billing
    in retry state; it never creates a permanent zero-cost consumption record.
    """

    from backend.services.usage_recorder import flush_task_usage
    from backend.services.usage_summary import refresh_task_summary

    try:
        await flush_task_usage(task_id)
    except Exception as exc:
        await mark_billing_retry(
            task_kind, task_id, RuntimeError(f"USAGE_WRITE_FAILED: {exc}")
        )
        return False
    try:
        await refresh_task_summary(task_id, strict=True)
    except Exception as exc:
        await mark_billing_retry(
            task_kind, task_id, RuntimeError(f"SUMMARY_REFRESH_FAILED: {exc}")
        )
        return False

    model = TASK_MODEL_BY_KIND.get(task_kind, ReviewTask)
    async with async_session_factory() as db:
        task = (
            await db.execute(select(model).where(model.id == task_id).with_for_update())
        ).scalar_one_or_none()
        if task is None or task.status not in TERMINAL_STATUSES:
            return False
        if task.billing_status == "legacy":
            return False
        task.usage_finalized_at = utc_now()
        if task.billing_status != "settled":
            task.billing_status = "pending"
            task.billing_error = None
        await db.commit()
    enqueue_billing_settlement(task_kind, task_id)
    return True


def enqueue_billing_settlement(task_kind: str, task_id: str, *, countdown: int = 0) -> None:
    try:
        from backend.tasks.billing_tasks import settle_task_billing

        settle_task_billing.apply_async(args=[task_kind, task_id], countdown=countdown)
    except Exception as exc:
        # Periodic reconciliation is the durable fallback.
        logger.error(
            "[billing] could not enqueue settlement: kind=%s task=%s error=%s",
            task_kind,
            task_id,
            exc,
        )


async def mark_billing_retry(task_kind: str, task_id: str, exc: Exception) -> None:
    model = TASK_MODEL_BY_KIND.get(task_kind, ReviewTask)
    async with async_session_factory() as db:
        task = (
            await db.execute(select(model).where(model.id == task_id).with_for_update())
        ).scalar_one_or_none()
        if task is None or task.billing_status in {"legacy", "settled"}:
            return
        task.billing_status = "retry"
        task.billing_error = str(exc)[:2_000]
        await db.commit()


async def claim_task_for_execution(db: AsyncSession, *, task_kind: str, task_id: str):
    """Claim an outbox-delivered task once, preventing duplicate provider work."""

    model = TASK_MODEL_BY_KIND.get(task_kind, ReviewTask)
    task = (
        await db.execute(select(model).where(model.id == task_id).with_for_update())
    ).scalar_one_or_none()
    if task is None:
        return None
    if getattr(task, "billing_status", "legacy") == "legacy":
        return None
    if isinstance(task, ReviewTask) and task.task_type != task_kind:
        return None
    allowed = (
        {"created", "waiting_for_document"}
        if task_kind == "blind_check"
        else {"pending"}
    )
    if task.status not in allowed:
        return None
    task.status = "running"
    task.started_at = task.started_at or utc_now()
    if isinstance(task, ReviewTask):
        task.last_heartbeat = utc_now()
    await db.commit()
    return task
