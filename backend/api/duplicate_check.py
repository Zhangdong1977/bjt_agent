"""API routes for technical bid duplicate checking."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from backend.api.deps import CurrentUser, DBSession, is_interior_user
from backend.config import get_settings
from backend.models import (
    AgentStep,
    Document,
    DuplicateResult,
    Project,
    ReviewTask,
    TodoItem,
)
from backend.schemas.duplicate_check import (
    DuplicateResultResponse,
    DuplicateResultsResponse,
    DuplicateSummary,
    DuplicateTodoResponse,
)
from backend.schemas.review import AgentStepResponse, ReviewTaskListItem, ReviewTaskResponse
from backend.services.sse_service import sse_manager
from backend.utils.time_utils import utc_now

router = APIRouter(
    prefix="/projects/{project_id}/duplicate-check", tags=["Duplicate Check"]
)

BLOCKED_EXTERNAL_EVENTS = {
    "step",
    "sub_agent_step",
    "sub_agent_step_start",
    "sub_agent_llm_output",
    "sub_agent_tool_call_start",
    "sub_agent_tool_call_end",
    "sub_agent_step_complete",
}


async def _project(
    project_id: str,
    current_user,
    db: DBSession,
    *,
    allow_interior_read: bool = False,
) -> Project:
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None or project.project_type != "duplicate":
        raise HTTPException(status_code=404, detail="查重项目不存在或无权访问")
    if allow_interior_read and is_interior_user(current_user):
        return project
    if project.user_id != current_user.id or project.is_deleted:
        raise HTTPException(status_code=404, detail="查重项目不存在或无权访问")
    return project


async def _task(project_id: str, task_id: str, db: DBSession) -> ReviewTask:
    task = (
        await db.execute(
            select(ReviewTask).where(
                ReviewTask.id == task_id,
                ReviewTask.project_id == project_id,
                ReviewTask.task_type == "duplicate",
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="查重任务不存在或已被删除")
    return task


@router.post("", response_model=ReviewTaskResponse, status_code=status.HTTP_201_CREATED)
async def start_duplicate_check(
    request: Request,
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ReviewTask:
    project = await _project(project_id, current_user, db)
    documents = list(
        (
            await db.execute(select(Document).where(Document.project_id == project_id))
        ).scalars().all()
    )
    left = [d for d in documents if d.doc_type == "duplicate_left"]
    right = [d for d in documents if d.doc_type == "duplicate_right"]
    if len(left) != 1 or len(right) != 1:
        raise HTTPException(status_code=400, detail="请分别上传一份 A 方和 B 方技术应标书")
    if any(document.status != "parsed" for document in (left[0], right[0])):
        raise HTTPException(status_code=400, detail="两份技术应标书必须全部解析完成")

    from backend.services.billing import ensure_wallet

    wallet = await ensure_wallet(db, current_user.id)
    if wallet.balance_wen <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "INSUFFICIENT_BALANCE",
                "message": "余额不足，请先充值后再发起 AI 查重",
                "balance_wen": wallet.balance_wen,
            },
        )

    running = list(
        (
            await db.execute(
                select(ReviewTask).where(
                    ReviewTask.project_id == project_id,
                    ReviewTask.task_type == "duplicate",
                    ReviewTask.status.in_(["pending", "running"]),
                )
            )
        ).scalars().all()
    )
    for stale in running:
        stale.status = "failed"
        stale.error_message = "上次异常中断的查重任务已自动结束，请重新发起"
        stale.completed_at = utc_now()

    from backend.api.deps import get_token_claims, oauth2_scheme

    token = await oauth2_scheme(request)
    claims = get_token_claims(token)
    concurrency = (
        claims.get("concurrency") or get_settings().max_sub_agent_concurrency
    )
    task = ReviewTask(
        project_id=project.id,
        task_type="duplicate",
        status="pending",
        max_concurrency=max(1, int(concurrency)),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    from backend.tasks.duplicate_tasks import run_duplicate_check

    try:
        celery_result = run_duplicate_check.delay(task.id)
    except Exception as exc:
        task.status = "failed"
        task.error_message = "任务队列暂不可用，请稍后重试"
        task.completed_at = utc_now()
        await db.commit()
        raise HTTPException(status_code=503, detail=task.error_message) from exc
    task.celery_task_id = celery_result.id
    await db.flush()
    return task


@router.get("/tasks", response_model=list[ReviewTaskListItem])
async def list_duplicate_tasks(
    project_id: str, db: DBSession, current_user: CurrentUser
) -> list[ReviewTask]:
    await _project(project_id, current_user, db, allow_interior_read=True)
    return list(
        (
            await db.execute(
                select(ReviewTask)
                .where(
                    ReviewTask.project_id == project_id,
                    ReviewTask.task_type == "duplicate",
                )
                .order_by(ReviewTask.created_at.desc())
            )
        ).scalars().all()
    )


@router.get("/tasks/{task_id}", response_model=ReviewTaskResponse)
async def get_duplicate_task(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> ReviewTask:
    await _project(project_id, current_user, db, allow_interior_read=True)
    return await _task(project_id, task_id, db)


@router.post("/tasks/{task_id}/cancel", response_model=ReviewTaskResponse)
async def cancel_duplicate_task(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> ReviewTask:
    await _project(project_id, current_user, db)
    task = await _task(project_id, task_id, db)
    if task.status not in {"pending", "running"}:
        raise HTTPException(status_code=400, detail="当前任务状态不可取消")
    if task.celery_task_id:
        from backend.celery_app import celery_app

        try:
            celery_app.control.revoke(task.celery_task_id, terminate=True)
        except Exception:
            pass
    from backend.tasks.review_tasks import set_task_cancelled

    set_task_cancelled(task_id)
    task.status = "cancelled"
    task.completed_at = utc_now()
    await db.commit()
    await db.refresh(task)
    return task


@router.post("/tasks/{task_id}/heartbeat")
async def heartbeat_duplicate_task(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> dict:
    await _project(project_id, current_user, db)
    task = await _task(project_id, task_id, db)
    if task.status != "running":
        return {"status": task.status, "message": "任务当前未在运行"}
    task.last_heartbeat = utc_now()
    await db.flush()
    return {"status": "ok", "last_heartbeat": task.last_heartbeat}


@router.get("/tasks/{task_id}/steps", response_model=list[AgentStepResponse])
async def get_duplicate_steps(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> list[AgentStep]:
    if not is_interior_user(current_user):
        raise HTTPException(status_code=403, detail="外部用户无权查看时间线")
    await _project(project_id, current_user, db, allow_interior_read=True)
    await _task(project_id, task_id, db)
    return list(
        (
            await db.execute(
                select(AgentStep)
                .where(AgentStep.task_id == task_id)
                .order_by(AgentStep.step_number.asc(), AgentStep.created_at.asc())
            )
        ).scalars().all()
    )


@router.get("/tasks/{task_id}/todos", response_model=list[DuplicateTodoResponse])
async def get_duplicate_todos(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> list[TodoItem]:
    await _project(project_id, current_user, db, allow_interior_read=True)
    await _task(project_id, task_id, db)
    return list(
        (
            await db.execute(
                select(TodoItem)
                .where(TodoItem.session_id == task_id)
                .order_by(TodoItem.created_at.asc())
            )
        ).scalars().all()
    )


@router.get("/tasks/{task_id}/results", response_model=DuplicateResultsResponse)
async def get_duplicate_results(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> DuplicateResultsResponse:
    await _project(project_id, current_user, db, allow_interior_read=True)
    await _task(project_id, task_id, db)
    todos = list(
        (
            await db.execute(
                select(TodoItem)
                .where(TodoItem.session_id == task_id)
                .order_by(TodoItem.created_at.asc())
            )
        ).scalars().all()
    )
    findings = list(
        (
            await db.execute(
                select(DuplicateResult)
                .where(DuplicateResult.task_id == task_id)
                .order_by(DuplicateResult.rule_doc_name, DuplicateResult.created_at)
            )
        ).scalars().all()
    )
    document_ids = {
        finding.left_document_id for finding in findings
    } | {finding.right_document_id for finding in findings}
    filenames = {}
    if document_ids:
        rows = await db.execute(
            select(Document.id, Document.original_filename).where(Document.id.in_(document_ids))
        )
        filenames = {doc_id: filename for doc_id, filename in rows.all()}
    response_findings = [
        DuplicateResultResponse.model_validate(finding).model_copy(
            update={
                "left_filename": filenames.get(finding.left_document_id),
                "right_filename": filenames.get(finding.right_document_id),
            }
        )
        for finding in findings
    ]
    reasonable_count = sum(item.verdict == "reasonable" for item in findings)
    suspicious_count = sum(item.verdict == "suspicious" for item in findings)
    summary = DuplicateSummary(
        rule_count=len(todos),
        completed_rule_count=sum(todo.status == "completed" for todo in todos),
        reasonable_count=reasonable_count,
        suspicious_count=suspicious_count,
    )
    return DuplicateResultsResponse(summary=summary, findings=response_findings, todos=todos)


@router.get("/tasks/{task_id}/stream")
async def stream_duplicate_events(
    project_id: str,
    task_id: str,
    request: Request,
    db: DBSession,
    current_user: CurrentUser,
):
    await _project(project_id, current_user, db)
    await _task(project_id, task_id, db)
    internal = is_interior_user(current_user)
    last_event_id = request.headers.get("Last-Event-ID")

    async def generator():
        async for event in sse_manager.connect(task_id, last_event_id):
            if internal:
                yield event
                continue
            blocked = False
            for line in event.splitlines():
                if line.startswith("data: "):
                    try:
                        blocked = json.loads(line[6:]).get("type") in BLOCKED_EXTERNAL_EVENTS
                    except json.JSONDecodeError:
                        blocked = False
                    break
            if not blocked:
                yield event

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
