"""Review API routes."""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from backend.api.deps import DBSession, CurrentUser, get_token_claims, oauth2_scheme, is_interior_user
from backend.config import get_settings
from backend.models import Project, ReviewTask, ReviewResult, AgentStep, TodoItem
from backend.utils.time_utils import utc_now
from backend.schemas.review import (
    ReviewResponse,
    ReviewResultResponse,
    ReviewTaskResponse,
    ReviewTaskListItem,
    AgentStepResponse,
    TodoItemResponse,
)
from backend.services.sse_service import sse_manager

router = APIRouter(prefix="/projects/{project_id}/review", tags=["Review"])
settings = get_settings()


async def verify_project_ownership(
    project_id: str, current_user, db: DBSession, *, allow_interior: bool = False,
) -> Project:
    """Verify that the project exists and the caller may access it.

    Regular users may only access their own projects. When ``allow_interior``
    is set, internal users (see :func:`is_interior_user`) may access any
    project — used by read-only / review endpoints surfaced on the experience
    dashboard. Write operations (start / cancel / heartbeat / live SSE) must
    keep ``allow_interior=False`` so internal users cannot mutate others' data.
    """
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在或无权访问",
        )
    if allow_interior and is_interior_user(current_user):
        return project
    if project.user_id != current_user.id or project.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在或无权访问",
        )
    return project


@router.get("/tasks", response_model=list[ReviewTaskListItem])
async def list_review_tasks(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> list[ReviewTaskListItem]:
    """List all review tasks for the project (newest first)."""
    await verify_project_ownership(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.project_id == project_id)
        .order_by(ReviewTask.created_at.desc())
    )
    tasks = result.scalars().all()
    return tasks


@router.post("", response_model=ReviewTaskResponse, status_code=status.HTTP_201_CREATED)
async def start_review(
    request: Request,
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ReviewTask:
    """Start a new review task for the project."""
    await verify_project_ownership(project_id, current_user, db)
    if not is_interior_user(current_user):
        from backend.services.billing import ensure_wallet

        wallet = await ensure_wallet(db, current_user.id)
        if wallet.balance_wen <= 0:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "INSUFFICIENT_BALANCE",
                    "message": "余额不足，请先充值后再发起 AI 检查",
                    "balance_wen": wallet.balance_wen,
                },
            )

    # Extract concurrency from JWT claims
    from backend.api.deps import oauth2_scheme, get_token_claims
    token = await oauth2_scheme(request)
    claims = get_token_claims(token)
    concurrency = claims.get("concurrency", settings.max_sub_agent_concurrency)

    # Check if there are running tasks and auto-cancel stale tasks
    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.project_id == project_id)
        .where(ReviewTask.status.in_(["pending", "running"]))
    )
    existing_tasks = result.scalars().all()
    for existing_task in existing_tasks:
        # Auto-cancel stale tasks from crashed workers - they can't complete
        existing_task.status = "failed"
        existing_task.error_message = "上次异常中断的审查任务已自动结束，请重新发起审查"
        existing_task.completed_at = utc_now()
    if existing_tasks:
        await db.flush()

    # Create new review task
    task = ReviewTask(
        project_id=project_id,
        status="pending",
        max_concurrency=concurrency,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    await db.commit()

    # Trigger the review task via Celery. If broker dispatch fails after the
    # task row is created, persist a terminal failed state so the UI never sees
    # a non-executable pending task.
    from backend.tasks.review_tasks import run_review
    try:
        celery_result = run_review.delay(task.id)
    except Exception as exc:
        logger.exception(
            "[start_review] Failed to dispatch review task to Celery: task_id=%s",
            task.id,
        )
        task.status = "failed"
        task.error_message = "任务队列暂不可用，请稍后重试"
        task.completed_at = utc_now()
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "REVIEW_QUEUE_UNAVAILABLE",
                "message": task.error_message,
                "task_id": task.id,
            },
        ) from exc

    task.celery_task_id = celery_result.id
    await db.flush()

    return task


@router.get("", response_model=ReviewResponse)
async def get_review_results(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ReviewResponse:
    """Get the review results for the project.

    Returns findings from the latest review task, grouped by sub-agent.
    """
    await verify_project_ownership(project_id, current_user, db, allow_interior=True)

    # Get the latest completed review task for this project
    latest_task_result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.project_id == project_id, ReviewTask.status == "completed")
        .order_by(ReviewTask.created_at.desc())
        .limit(1)
    )
    latest_task = latest_task_result.scalar_one_or_none()

    if not latest_task:
        return ReviewResponse(
            summary={"category_count": 0, "check_item_count": 0, "risk_item_count": 0},
            findings=[],
        )

    # Get findings from ReviewResult for the latest task
    result = await db.execute(
        select(ReviewResult)
        .where(ReviewResult.task_id == latest_task.id)
        .order_by(
            ReviewResult.severity.asc(),
            ReviewResult.created_at.asc(),
        )
    )
    findings = result.scalars().all()
    logger.info(f"[get_review_results] project_id={project_id}, task_id={latest_task.id}, findings_count={len(findings)}")

    # Calculate summary
    category_count_result = await db.execute(
        select(func.count()).where(TodoItem.session_id == latest_task.id)
    )
    category_count = category_count_result.scalar()

    check_item_count_result = await db.execute(
        select(TodoItem.check_items).where(TodoItem.session_id == latest_task.id)
    )
    check_items_rows = check_item_count_result.all()
    check_item_count = sum(len(row[0] or []) for row in check_items_rows)

    summary = {
        "category_count": category_count,
        "check_item_count": check_item_count,
        "risk_item_count": len({f.check_item_name for f in findings if not f.is_compliant and f.check_item_name}),
    }

    return ReviewResponse(summary=summary, findings=findings)


# Task-specific endpoints
@router.get("/tasks/{task_id}", response_model=ReviewTaskResponse)
async def get_review_task_status(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ReviewTask:
    """Get the status of a specific review task."""
    await verify_project_ownership(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.id == task_id, ReviewTask.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="审查任务不存在或已被删除",
        )
    return task


@router.post("/tasks/{task_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_review_task(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ReviewTaskResponse:
    """Cancel a running review task."""
    await verify_project_ownership(project_id, current_user, db)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.id == task_id, ReviewTask.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="审查任务不存在或已被删除",
        )

    if task.status not in ["pending", "running"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前任务状态不可取消",
        )

    # Revoke Celery task if running
    if task.celery_task_id:
        from backend.celery_app import celery_app

        try:
            celery_app.control.revoke(task.celery_task_id, terminate=True)
        except Exception:
            pass  # Task may have already completed or expired

    # Set Redis cancellation flag so the heartbeat monitor can detect it
    from backend.tasks.review_tasks import set_task_cancelled
    set_task_cancelled(task_id)

    task.status = "cancelled"
    task.completed_at = utc_now()
    await db.flush()
    await db.refresh(task)
    return task


@router.post("/tasks/{task_id}/heartbeat", status_code=status.HTTP_200_OK)
async def heartbeat_review_task(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """Update the last_heartbeat timestamp for a running review task.

    This endpoint should be called by the frontend every 10 seconds while
    the user is viewing the task progress page. If no heartbeat is received
    for 20+ seconds, the task will be automatically cancelled.
    """
    await verify_project_ownership(project_id, current_user, db)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.id == task_id, ReviewTask.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="审查任务不存在或已被删除",
        )

    if task.status != "running":
        # Still return 200 for non-running tasks to avoid frontend errors
        return {"status": task.status, "message": "任务当前未在运行"}

    task.last_heartbeat = utc_now()
    await db.flush()
    return {"status": "ok", "last_heartbeat": task.last_heartbeat}


@router.get("/tasks/{task_id}/steps", response_model=list[AgentStepResponse])
async def get_review_task_steps(
    project_id: str,
    task_id: str,
    request: Request,
    db: DBSession,
    current_user: CurrentUser,
) -> list[AgentStep]:
    """Get all steps for a review task (for timeline display). Internal users only."""
    token = await oauth2_scheme(request)
    claims = get_token_claims(token)
    if not claims["interior_user"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="外部用户无权查看时间线",
        )

    await verify_project_ownership(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.id == task_id, ReviewTask.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="审查任务不存在或已被删除",
        )

    result = await db.execute(
        select(AgentStep)
        .where(AgentStep.task_id == task_id)
        .order_by(AgentStep.step_number.asc())
    )
    steps = result.scalars().all()
    return steps


@router.get("/tasks/{task_id}/results", response_model=list[ReviewResultResponse])
async def get_review_task_results(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> list[ReviewResult]:
    """Get all findings for a review task."""
    await verify_project_ownership(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.id == task_id, ReviewTask.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="审查任务不存在或已被删除",
        )

    result = await db.execute(
        select(ReviewResult)
        .where(ReviewResult.task_id == task_id)
        .order_by(
            ReviewResult.severity.asc(),
            ReviewResult.created_at.asc(),
        )
    )
    findings = result.scalars().all()
    return findings


@router.get("/tasks/{task_id}/todos", response_model=list[TodoItemResponse])
async def get_review_task_todos(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> list[TodoItem]:
    """Get all todo items (sub-agents) for a review task.

    TodoItems are created during review execution with session_id = task_id.
    This endpoint allows fetching sub-agent metadata (name, status) for block-headers.
    Detailed findings within todos are shown in block-body which is hidden via
    allowExpand=false for external users.
    """
    await verify_project_ownership(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.id == task_id, ReviewTask.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="审查任务不存在或已被删除",
        )

    result = await db.execute(
        select(TodoItem)
        .where(TodoItem.session_id == task_id)
        .order_by(TodoItem.created_at.asc())
    )
    todos = result.scalars().all()
    return todos


@router.get("/tasks/{task_id}/todos/{todo_id}/report")
async def get_todo_report(
    project_id: str,
    task_id: str,
    todo_id: str,
    db: DBSession,
    current_user: CurrentUser,
):
    """Fetch the markdown report file content for a specific todo (sub-agent).

    Returns the raw markdown text of the review report generated by the sub-agent.
    """
    from pathlib import Path as FilePath
    from fastapi.responses import PlainTextResponse

    # allow_interior: internal users review others' projects; resolve the
    # report under the project OWNER's workspace, not the viewer's.
    project = await verify_project_ownership(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(TodoItem).where(
            TodoItem.id == todo_id,
            TodoItem.session_id == task_id,
            TodoItem.project_id == project_id,
        )
    )
    todo = result.scalar_one_or_none()
    if not todo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="检查项不存在或已被删除",
        )

    report_path = (todo.result or {}).get("report_path")

    # Fallback: scan workspace for review_*.md files if report_path not stored
    if not report_path:
        workspace_dir = settings.workspace_path / str(project.user_id) / project_id
        if workspace_dir.exists():
            review_files = sorted(workspace_dir.glob("review_*.md"))
            if review_files:
                report_path = str(review_files[-1])

    if not report_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="审查报告尚未生成",
        )

    report_file = FilePath(report_path)
    if not report_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="审查报告文件不存在，请重新生成",
        )

    content = report_file.read_text(encoding="utf-8")
    return PlainTextResponse(content=content, media_type="text/markdown")


@router.get("/tasks/{task_id}/stream")
async def stream_review_events(
    project_id: str,
    task_id: str,
    request: Request,
    db: DBSession,
    current_user: CurrentUser,
):
    """Stream SSE events for a review task.

    This endpoint provides real-time updates about the review task progress,
    including agent steps, progress updates, and completion status.
    Supports reconnection via Last-Event-ID header.

    External users receive filtered events: only status-level events
    (block-header info) are forwarded; detailed step/timeline data is skipped.
    """
    token = await oauth2_scheme(request)
    claims = get_token_claims(token)
    is_internal = claims["interior_user"]

    # Verify user has access to the project
    await verify_project_ownership(project_id, current_user, db)

    # Verify task exists and belongs to this project
    result = await db.execute(
        select(ReviewTask).where(
            ReviewTask.id == task_id,
            ReviewTask.project_id == project_id,
        )
    )
    task = result.scalars().first()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在或已被删除",
        )

    # Events that contain detailed step/timeline data — blocked for external users
    BLOCKED_EVENTS = {
        "step",
        "sub_agent_step",
        "sub_agent_step_start",
        "sub_agent_llm_output",
        "sub_agent_tool_call_start",
        "sub_agent_tool_call_end",
        "sub_agent_step_complete",
    }

    # Extract Last-Event-ID header for reconnection support
    last_event_id = request.headers.get("Last-Event-ID")

    async def event_generator():
        async for event in sse_manager.connect(task_id, last_event_id):
            if is_internal:
                yield event
                continue

            # Filter for external users: skip blocked event types
            for line in event.splitlines():
                if line.startswith("data: "):
                    try:
                        json_data = line[6:]  # strip "data: " prefix
                        data = json.loads(json_data)
                        if data.get("type") in BLOCKED_EVENTS:
                            break  # skip this entire event
                    except (json.JSONDecodeError, KeyError):
                        pass
            else:
                yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
