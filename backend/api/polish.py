"""VSTO-driven polish/expand/abbreviate task APIs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from backend.api.deps import CurrentUser, DBSession
from backend.models import PolishTask
from backend.schemas.polish import PolishTaskCreate, PolishTaskResponse
from backend.utils.time_utils import utc_now

router = APIRouter(prefix="/polish", tags=["Polish"])


async def _owned_task(task_id: str, current_user, db: DBSession) -> PolishTask:
    task = (
        await db.execute(select(PolishTask).where(PolishTask.id == task_id))
    ).scalar_one_or_none()
    if task is None or task.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="润色任务不存在或无权访问")
    return task


@router.post("/tasks", response_model=PolishTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: PolishTaskCreate,
    db: DBSession,
    current_user: CurrentUser,
) -> PolishTask:
    """Create and enqueue one polish/expand/abbreviate run."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="待处理文本不能为空")

    from backend.services.task_lifecycle import (
        add_task_dispatch,
        authorize_billable_task_start,
        dispatch_task_outbox,
    )

    sales_config = await authorize_billable_task_start(db, user_id=current_user.id, operation_name="AI 润色")
    from backend.services.sales import multiplier_for_task

    task = PolishTask(
        user_id=current_user.id,
        mode=body.mode,
        input_text=text,
        requirements=(body.requirements or "").strip() or None,
        target_length=body.target_length,
        status="pending",
        billing_multiplier=multiplier_for_task(sales_config, "polish"),
        billing_status="pending",
    )
    db.add(task)
    await db.flush()
    outbox = add_task_dispatch(db, task_kind="polish", task_id=task.id)
    await db.flush()
    task.celery_task_id = outbox.celery_task_id
    await db.commit()
    await db.refresh(task)
    await dispatch_task_outbox(outbox.id)
    return task


@router.get("/tasks/{task_id}", response_model=PolishTaskResponse)
async def get_task(task_id: str, db: DBSession, current_user: CurrentUser) -> PolishTask:
    return await _owned_task(task_id, current_user, db)


@router.post("/tasks/{task_id}/cancel", response_model=PolishTaskResponse)
async def cancel_task(task_id: str, db: DBSession, current_user: CurrentUser) -> PolishTask:
    task = await _owned_task(task_id, current_user, db)
    if task.status in {"completed", "failed", "cancelled"}:
        return task
    from backend.services.task_lifecycle import (
        cancel_pending_dispatch,
        enqueue_billing_settlement,
        finalize_task_usage,
    )

    cancelled_before_dispatch = await cancel_pending_dispatch(db, task_kind="polish", task_id=task_id)
    if task.celery_task_id and not cancelled_before_dispatch:
        try:
            from backend.celery_app import celery_app

            celery_app.control.revoke(task.celery_task_id, terminate=False)
        except Exception:
            pass
    try:
        from backend.tasks.polish_tasks import set_polish_cancelled

        set_polish_cancelled(task.id)
    except Exception:
        if task.celery_task_id and not cancelled_before_dispatch:
            from backend.celery_app import celery_app

            celery_app.control.revoke(task.celery_task_id, terminate=True)
    task.status = "cancelled"
    task.error_message = "用户取消了润色任务"
    task.completed_at = utc_now()
    task.billing_status = "pending"
    if cancelled_before_dispatch:
        task.usage_finalized_at = utc_now()
    await db.commit()
    await db.refresh(task)
    if cancelled_before_dispatch:
        await finalize_task_usage("polish", task_id)
    else:
        from backend.config import get_settings

        enqueue_billing_settlement(
            "polish",
            task_id,
            countdown=get_settings().billing_orphan_finalize_grace_seconds,
        )
    return task
