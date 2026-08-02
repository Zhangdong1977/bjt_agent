"""Authenticated VSTO tool-session and result relay APIs."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from backend.api.deps import CurrentUser, DBSession
from backend.models import BlindCheckTask, VstoToolCall, VstoToolSession
from backend.schemas.blind_check import (
    VstoToolResultRequest,
    VstoToolSessionCreate,
    VstoToolSessionResponse,
)
from backend.services.vsto_tool_broker import (
    VSTO_TOOL_NAMES,
    discard_tool_result,
    publish_tool_result,
    validate_tool_result_payload,
)
from backend.utils.time_utils import utc_now

router = APIRouter(prefix="/vsto-tools", tags=["VSTO Tools"])
SESSION_TTL_MINUTES = 30


async def _session(session_id: str, current_user, db: DBSession, *, allow_closed: bool = False):
    row = (
        await db.execute(select(VstoToolSession).where(VstoToolSession.id == session_id))
    ).scalar_one_or_none()
    now = utc_now()
    if row is None or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="VSTO 工具会话不存在")
    if not allow_closed and (row.status != "active" or row.expires_at <= now):
        raise HTTPException(status_code=409, detail="VSTO 工具会话已失效，请重新打开 Word 页面")
    return row


@router.post("/sessions", response_model=VstoToolSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: VstoToolSessionCreate,
    db: DBSession,
    current_user: CurrentUser,
) -> VstoToolSession:
    now = utc_now()
    session = VstoToolSession(
        user_id=current_user.id,
        client_instance_id=body.client_instance_id,
        document_name=body.document_name,
        document_key=body.document_key,
        document_revision=body.document_revision,
        snapshot_id=body.snapshot_id,
        status="active",
        last_seen_at=now,
        expires_at=now + timedelta(minutes=SESSION_TTL_MINUTES),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.get("/sessions/{session_id}", response_model=VstoToolSessionResponse)
async def get_session(session_id: str, db: DBSession, current_user: CurrentUser):
    return await _session(session_id, current_user, db)


@router.post("/sessions/{session_id}/heartbeat", response_model=VstoToolSessionResponse)
async def heartbeat_session(session_id: str, db: DBSession, current_user: CurrentUser):
    session = await _session(session_id, current_user, db)
    now = utc_now()
    session.last_seen_at = now
    session.expires_at = now + timedelta(minutes=SESSION_TTL_MINUTES)
    await db.commit()
    await db.refresh(session)
    return session


@router.post("/sessions/{session_id}/close")
async def close_session(session_id: str, db: DBSession, current_user: CurrentUser):
    session = await _session(session_id, current_user, db, allow_closed=True)
    now = utc_now()
    pending_calls = (
        await db.execute(
            select(VstoToolCall).where(
                VstoToolCall.session_id == session.id,
                VstoToolCall.status == "pending",
            )
        )
    ).scalars().all()
    cancelled_task_ids: set[str] = set()
    for call in pending_calls:
        call.status = "failed"
        call.error_message = "Word 工具会话已关闭"
        call.result = {
            "success": False,
            "data": {},
            "content": "",
            "error": call.error_message,
        }
        call.answered_at = now
        cancelled_task_ids.add(call.task_id)
    session.status = "closed"
    session.last_seen_at = now
    tasks = (
        await db.execute(
            select(BlindCheckTask).where(
                BlindCheckTask.tool_session_id == session.id,
                BlindCheckTask.status.in_(["created", "waiting_for_document", "running"]),
            )
        )
    ).scalars().all()
    for task in tasks:
        task.status = "cancelled"
        task.error_message = "Word 页面已关闭，检查已取消"
        task.completed_at = now
        cancelled_task_ids.add(task.id)
    await db.commit()
    for call in pending_calls:
        discard_tool_result(call.call_id)
        publish_tool_result(call.call_id, call.result)
    if cancelled_task_ids:
        try:
            from backend.tasks.blind_check_tasks import set_blind_check_cancelled

            for task_id in cancelled_task_ids:
                set_blind_check_cancelled(task_id)
        except Exception:
            pass
    return {"session_id": session.id, "status": session.status}


@router.post("/results")
async def submit_tool_result(
    body: VstoToolResultRequest,
    db: DBSession,
    current_user: CurrentUser,
):
    """Accept a VSTO result and wake the waiting Celery agent."""
    session = await _session(body.tool_session_id, current_user, db)
    call = (
        await db.execute(
            select(VstoToolCall)
            .where(VstoToolCall.call_id == body.call_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if call is None or call.session_id != session.id:
        raise HTTPException(status_code=404, detail="工具调用不存在或不属于当前会话")
    task = (
        await db.execute(select(BlindCheckTask).where(BlindCheckTask.id == call.task_id))
    ).scalar_one_or_none()
    if task is None or task.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="暗标检查任务不存在")
    if call.tool_name not in VSTO_TOOL_NAMES:
        raise HTTPException(status_code=400, detail="工具不在允许列表中")
    if call.status in {"completed", "failed", "expired"}:
        return {"call_id": call.call_id, "status": call.status, "idempotent": True}
    if task.status not in {"created", "waiting_for_document", "running"}:
        error = "暗标检查任务已结束，拒绝接收迟到的 Word 工具结果"
        call.status = "failed"
        call.error_message = error
        call.result = {
            "success": False,
            "data": {},
            "content": "",
            "error": error,
        }
        call.answered_at = utc_now()
        await db.commit()
        publish_tool_result(call.call_id, call.result)
        raise HTTPException(status_code=409, detail=error)
    if call.expires_at <= utc_now():
        call.status = "expired"
        call.error_message = "工具调用已超时"
        await db.commit()
        raise HTTPException(status_code=409, detail="工具调用已超时，请重新发起检查")

    expected_snapshot_id = None
    if isinstance(call.arguments, dict):
        expected_snapshot_id = call.arguments.get("snapshot_id")
    snapshot_changed = bool(
        body.success
        and expected_snapshot_id
        and body.snapshot_id != expected_snapshot_id
    )
    success = bool(body.success) and not snapshot_changed
    data = body.data if isinstance(body.data, dict) else {}
    content = (body.content or "")[:256_000]
    error = (body.error or "")[:2_000] if not success else None
    if snapshot_changed:
        error = "Word 文档快照已变化，本次工具结果已作废，请重新检查"
        data = {}
        content = ""
    result = {
        "success": success,
        "data": data,
        "content": content,
        "error": error,
        "snapshot_id": body.snapshot_id,
    }
    try:
        validate_tool_result_payload(result)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    call.status = "completed" if success else "failed"
    call.result = result
    call.error_message = error
    call.answered_at = utc_now()
    if body.snapshot_id and not session.snapshot_id:
        session.snapshot_id = body.snapshot_id
        task.snapshot_id = body.snapshot_id
    session.last_seen_at = utc_now()
    await db.commit()
    publish_tool_result(call.call_id, result)
    return {"call_id": call.call_id, "status": call.status}
