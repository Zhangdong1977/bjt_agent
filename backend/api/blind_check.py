"""Blind-mark compliance check task APIs."""

from __future__ import annotations

import json
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from backend.api.deps import CurrentUser, DBSession
from backend.models import BlindCheckFinding, BlindCheckTask, VstoToolCall, VstoToolSession
from backend.schemas.blind_check import (
    BlindCheckFindingResponse,
    BlindCheckResultsResponse,
    BlindCheckTaskCreate,
    BlindCheckTaskResponse,
)
from backend.services.sse_service import sse_manager
from backend.utils.time_utils import utc_now

router = APIRouter(prefix="/blind-check", tags=["Blind Check"])


async def _owned_session(session_id: str, current_user, db: DBSession) -> VstoToolSession:
    session = (
        await db.execute(select(VstoToolSession).where(VstoToolSession.id == session_id))
    ).scalar_one_or_none()
    now = utc_now()
    if (
        session is None
        or session.user_id != current_user.id
        or session.status != "active"
        or session.expires_at <= now
    ):
        raise HTTPException(status_code=404, detail="VSTO 工具会话不存在或已失效，请重新打开检查页面")
    return session


async def _owned_task(task_id: str, current_user, db: DBSession) -> BlindCheckTask:
    task = (
        await db.execute(select(BlindCheckTask).where(BlindCheckTask.id == task_id))
    ).scalar_one_or_none()
    if task is None or task.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="暗标检查任务不存在或无权访问")
    return task


@router.post("/tasks", response_model=BlindCheckTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: BlindCheckTaskCreate,
    db: DBSession,
    current_user: CurrentUser,
) -> BlindCheckTask:
    """Create and enqueue one blind-mark check."""
    session = await _owned_session(body.tool_session_id, current_user, db)
    requirement_text = body.requirement_text.strip()
    if not requirement_text:
        raise HTTPException(status_code=422, detail="暗标要求不能为空")
    if not session.document_key or not session.document_revision or not session.snapshot_id:
        raise HTTPException(status_code=409, detail="工具会话尚未绑定 Word 文档，请在插件页面重新连接")
    if body.document_key and body.document_key != session.document_key:
        raise HTTPException(status_code=409, detail="Word 文档已切换，请刷新页面后重新提交")
    if body.document_revision and body.document_revision != session.document_revision:
        raise HTTPException(status_code=409, detail="Word 文档已修改，请重新建立检查任务")

    from backend.services.task_lifecycle import (
        add_task_dispatch,
        authorize_billable_task_start,
        dispatch_task_outbox,
    )

    sales_config = await authorize_billable_task_start(
        db, user_id=current_user.id, operation_name="暗标检查"
    )
    now = utc_now()
    task = BlindCheckTask(
        user_id=current_user.id,
        tool_session_id=session.id,
        requirement_text=requirement_text,
        document_name=session.document_name or body.document_name,
        document_key=session.document_key,
        document_revision=session.document_revision,
        snapshot_id=session.snapshot_id,
        scope=body.scope.model_dump(mode="json") if body.scope is not None else None,
        status="waiting_for_document",
        billing_multiplier=sales_config.sales_multiplier,
        billing_status="pending",
    )
    session.last_seen_at = now
    db.add(task)
    await db.flush()
    outbox = add_task_dispatch(db, task_kind="blind_check", task_id=task.id)
    await db.flush()
    task.celery_task_id = outbox.celery_task_id
    await db.commit()
    await db.refresh(task)
    await dispatch_task_outbox(outbox.id)
    return task


@router.get("/tasks/{task_id}", response_model=BlindCheckTaskResponse)
async def get_task(task_id: str, db: DBSession, current_user: CurrentUser) -> BlindCheckTask:
    return await _owned_task(task_id, current_user, db)


@router.get("/tasks/{task_id}/results", response_model=BlindCheckResultsResponse)
async def get_results(
    task_id: str, db: DBSession, current_user: CurrentUser
) -> BlindCheckResultsResponse:
    task = await _owned_task(task_id, current_user, db)
    findings = (
        await db.execute(
            select(BlindCheckFinding)
            .where(BlindCheckFinding.task_id == task.id)
            .order_by(BlindCheckFinding.created_at.asc())
        )
    ).scalars().all()
    return BlindCheckResultsResponse(
        task_id=task.id,
        status=task.status,
        summary=task.summary,
        findings=[BlindCheckFindingResponse.model_validate(item) for item in findings],
    )


@router.post("/tasks/{task_id}/cancel", response_model=BlindCheckTaskResponse)
async def cancel_task(task_id: str, db: DBSession, current_user: CurrentUser) -> BlindCheckTask:
    task = await _owned_task(task_id, current_user, db)
    if task.status in {"completed", "failed", "cancelled"}:
        return task
    from backend.services.task_lifecycle import (
        cancel_pending_dispatch,
        enqueue_billing_settlement,
        finalize_task_usage,
    )

    cancelled_before_dispatch = await cancel_pending_dispatch(
        db, task_kind="blind_check", task_id=task_id
    )
    if task.celery_task_id and not cancelled_before_dispatch:
        try:
            from backend.celery_app import celery_app

            celery_app.control.revoke(task.celery_task_id, terminate=False)
        except Exception:
            # The durable status/cancel flag below is authoritative; revoke is
            # only a best-effort optimization.
            pass
    pending_calls = (
        await db.execute(
            select(VstoToolCall).where(
                VstoToolCall.task_id == task.id,
                VstoToolCall.status == "pending",
            )
        )
    ).scalars().all()
    now = utc_now()
    for call in pending_calls:
        call.status = "failed"
        call.error_message = "暗标检查任务已取消"
        call.result = {
            "success": False,
            "data": {},
            "content": "",
            "error": call.error_message,
        }
        call.answered_at = now
    try:
        from backend.tasks.blind_check_tasks import set_blind_check_cancelled

        set_blind_check_cancelled(task.id)
    except Exception:
        if task.celery_task_id and not cancelled_before_dispatch:
            from backend.celery_app import celery_app

            celery_app.control.revoke(task.celery_task_id, terminate=True)
    task.status = "cancelled"
    task.error_message = "用户取消了暗标检查"
    task.completed_at = now
    task.billing_status = "pending"
    if cancelled_before_dispatch:
        task.usage_finalized_at = now
    await db.commit()
    try:
        from backend.services.vsto_tool_broker import discard_tool_result, publish_tool_result

        for call in pending_calls:
            discard_tool_result(call.call_id)
            publish_tool_result(call.call_id, call.result)
    except Exception:
        # The durable call status is enough for the broker's DB polling path;
        # Redis publication is only a latency optimization.
        pass
    await db.refresh(task)
    if cancelled_before_dispatch:
        await finalize_task_usage("blind_check", task_id)
    else:
        from backend.config import get_settings

        enqueue_billing_settlement(
            "blind_check",
            task_id,
            countdown=get_settings().billing_orphan_finalize_grace_seconds,
        )
    return task


@router.get("/tasks/{task_id}/stream")
async def stream_task_events(
    task_id: str,
    request: Request,
    db: DBSession,
    current_user: CurrentUser,
):
    """Stream task progress; frontend uses fetch so Authorization is retained."""
    await _owned_task(task_id, current_user, db)
    last_event_id = request.headers.get("Last-Event-ID")

    async def generator() -> AsyncGenerator[str, None]:
        async for event in sse_manager.connect(task_id, last_event_id):
            yield event

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
