"""Review API routes."""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
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
    ReviewStartRequest,
    RuleDocInfo,
    RuleDocsResponse,
    AgentStepResponse,
    TodoItemResponse,
)
from backend.services.sse_service import sse_manager

router = APIRouter(prefix="/projects/{project_id}/review", tags=["Review"])
# 全局路由（不挂在项目下）：发起检查前的检查项大类列表在项目创建之前就要展示
rule_docs_router = APIRouter(prefix="/review", tags=["Review"])
settings = get_settings()

# 发起检查弹窗里默认不勾选的检查项大类（按规则文档编号前缀匹配）
DEFAULT_UNSELECTED_RULE_DOC_CODES = ("E001",)


def _rule_doc_code(name: str) -> str:
    """规则文档编号：文件名第一个空格前的部分，如 'E001 签字盖章检查.md' → 'E001'."""
    return name.split(" ", 1)[0]


@rule_docs_router.get("/rule-docs", response_model=RuleDocsResponse)
async def list_rule_docs(current_user: CurrentUser) -> RuleDocsResponse:
    """List check-item categories (rule library docs) for the start dialog."""
    from backend.agent.master.tools.rule_parser import RuleLibraryScannerTool

    scanner = RuleLibraryScannerTool()
    scan_result = await scanner.execute(str(settings.rule_library_path))
    if not scan_result.success:
        logger.error(f"[list_rule_docs] Rule library scan failed: {scan_result.error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="检查项规则库不可用，请稍后重试",
        )

    rule_docs = json.loads(scan_result.content)["rule_docs"]
    if not rule_docs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="检查项规则库为空，请联系管理员",
        )

    return RuleDocsResponse(
        rule_docs=[
            RuleDocInfo(
                name=d["name"],
                stem=d["stem"],
                default_selected=_rule_doc_code(d["name"]) not in DEFAULT_UNSELECTED_RULE_DOC_CODES,
            )
            for d in rule_docs
        ]
    )


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
        if project.project_type != "review":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="查重项目不能调用标书审查接口",
            )
        return project
    if project.user_id != current_user.id or project.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在或无权访问",
        )
    if project.project_type != "review":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="查重项目不能调用标书审查接口",
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
        .where(
            ReviewTask.project_id == project_id,
            ReviewTask.task_type == "review",
        )
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
    payload: ReviewStartRequest | None = None,
) -> ReviewTask:
    """Start a new review task for the project."""
    await verify_project_ownership(project_id, current_user, db)

    selected_rule_docs: list[str] | None = None
    if payload is not None and payload.selected_rule_docs is not None:
        if not payload.selected_rule_docs:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请至少选择一个检查项大类",
            )
        # 去重并保持勾选顺序
        selected_rule_docs = list(dict.fromkeys(payload.selected_rule_docs))

    from backend.services.task_lifecycle import (
        add_task_dispatch,
        authorize_billable_task_start,
        dispatch_task_outbox,
    )

    sales_config = await authorize_billable_task_start(
        db, user_id=current_user.id, operation_name=" AI 检查"
    )

    # Extract concurrency from JWT claims
    from backend.api.deps import oauth2_scheme, get_token_claims
    from backend.services.sales import multiplier_for_task
    token = await oauth2_scheme(request)
    claims = get_token_claims(token)
    concurrency = claims.get("concurrency", settings.max_sub_agent_concurrency)

    # Create new review task
    task = ReviewTask(
        project_id=project_id,
        task_type="review",
        status="pending",
        max_concurrency=concurrency,
        billing_multiplier=multiplier_for_task(sales_config, "review"),
        billing_status="pending",
        selected_rule_docs=selected_rule_docs,
    )
    db.add(task)
    await db.flush()
    outbox = add_task_dispatch(db, task_kind="review", task_id=task.id)
    await db.flush()
    task.celery_task_id = outbox.celery_task_id
    await db.refresh(task)
    await db.commit()

    # Opportunistic immediate delivery; the committed outbox is the durable
    # retry source if Redis/Celery is temporarily unavailable.
    await dispatch_task_outbox(outbox.id)

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
        .where(
            ReviewTask.project_id == project_id,
            ReviewTask.task_type == "review",
            ReviewTask.status == "completed",
        )
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
        .where(
            ReviewTask.id == task_id,
            ReviewTask.project_id == project_id,
            ReviewTask.task_type == "review",
        )
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
        .where(
            ReviewTask.id == task_id,
            ReviewTask.project_id == project_id,
            ReviewTask.task_type == "review",
        )
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

    from backend.services.task_lifecycle import (
        cancel_pending_dispatch,
        enqueue_billing_settlement,
        finalize_task_usage,
    )

    cancelled_before_dispatch = await cancel_pending_dispatch(
        db, task_kind="review", task_id=task_id
    )
    # Revoke Celery task if it was already delivered.
    if task.celery_task_id and not cancelled_before_dispatch:
        from backend.celery_app import celery_app

        try:
            celery_app.control.revoke(task.celery_task_id, terminate=False)
        except Exception:
            pass  # Task may have already completed or expired

    # Set Redis cancellation flag so the heartbeat monitor can detect it
    from backend.tasks.review_tasks import set_task_cancelled
    try:
        set_task_cancelled(task_id)
    except Exception:
        logger.exception("[cancel_review] Redis cancellation flag failed: task=%s", task_id)
        if task.celery_task_id and not cancelled_before_dispatch:
            celery_app.control.revoke(task.celery_task_id, terminate=True)

    task.status = "cancelled"
    task.completed_at = utc_now()
    task.billing_status = "pending"
    if cancelled_before_dispatch:
        task.usage_finalized_at = task.completed_at
    await db.commit()
    await db.refresh(task)
    if cancelled_before_dispatch:
        await finalize_task_usage("review", task_id)
    else:
        enqueue_billing_settlement(
            "review",
            task_id,
            countdown=get_settings().billing_orphan_finalize_grace_seconds,
        )
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
        .where(
            ReviewTask.id == task_id,
            ReviewTask.project_id == project_id,
            ReviewTask.task_type == "review",
        )
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
        .where(
            ReviewTask.id == task_id,
            ReviewTask.project_id == project_id,
            ReviewTask.task_type == "review",
        )
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
        .where(
            ReviewTask.id == task_id,
            ReviewTask.project_id == project_id,
            ReviewTask.task_type == "review",
        )
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
        .where(
            ReviewTask.id == task_id,
            ReviewTask.project_id == project_id,
            ReviewTask.task_type == "review",
        )
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
            ReviewTask.task_type == "review",
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


@router.get("/tasks/{task_id}/export.pdf")
async def export_review_pdf(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
):
    """Export the review results of a task as a structured PDF.

    Chapters are ordered by check-category (``rule_doc_name``) dictionary
    order. Each category lists its findings as a card with compliance status,
    severity, location, requirement, issue and suggestion. Empty results are
    allowed and produce a minimal "no results" PDF.
    """
    from backend.services.pdf_export import build_review_pdf

    project = await verify_project_ownership(
        project_id, current_user, db, allow_interior=True
    )

    result = await db.execute(
        select(ReviewTask).where(
            ReviewTask.id == task_id,
            ReviewTask.project_id == project_id,
            ReviewTask.task_type == "review",
        )
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

    # Group by rule_doc_name (fallback requirement_key), mirroring the UI's
    # grouping key in ReviewResultsArea.vue. PDF chapter order = label dict
    # order, applied inside build_review_pdf.
    groups_map: dict[str, list] = {}
    order: list[str] = []
    for f in findings:
        key = f.rule_doc_name or f.requirement_key
        if key not in groups_map:
            groups_map[key] = []
            order.append(key)
        groups_map[key].append(f)

    groups = []
    for key in order:
        items = groups_map[key]
        non_compliant = [f for f in items if not f.is_compliant]
        groups.append({
            "label": (key or "").replace(".md", "") or "未分类",
            "is_compliant": len(non_compliant) == 0,
            "non_compliant_count": len(non_compliant),
            "findings": items,
        })

    # Summary mirrors get_review_results so the PDF matches the on-screen stats.
    category_count_result = await db.execute(
        select(func.count()).where(TodoItem.session_id == task_id)
    )
    category_count = category_count_result.scalar() or 0

    check_item_count_result = await db.execute(
        select(TodoItem.check_items).where(TodoItem.session_id == task_id)
    )
    check_items_rows = check_item_count_result.all()
    check_item_count = sum(len(row[0] or []) for row in check_items_rows)

    summary = {
        "category_count": category_count,
        "check_item_count": check_item_count,
        "risk_item_count": len({
            f.check_item_name for f in findings
            if not f.is_compliant and f.check_item_name
        }),
    }

    try:
        pdf_bytes = build_review_pdf(
            project.name, task.completed_at, summary, groups,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("[export_review_pdf] build failed task=%s", task_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="生成 PDF 失败，请稍后重试",
        )

    filename = f"review-{str(task_id)[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
