"""Bid draft generation task APIs (招标解析 → 标书生成)."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from backend.api.deps import CurrentUser, DBSession
from backend.config import get_settings
from backend.models import BidDraftSection, BidDraftTask, Document, Project
from backend.schemas.bid_draft import (
    BidDraftAssembledResponse,
    BidDraftSectionContentResponse,
    BidDraftSectionResponse,
    BidDraftTaskCreate,
    BidDraftTaskResponse,
)
from backend.services.sse_service import sse_manager
from backend.utils.time_utils import utc_now

router = APIRouter(prefix="/bid-draft", tags=["Bid Draft"])

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _node_sort_key(node_id: str) -> tuple[int, ...]:
    """Natural sort key for hierarchical node ids ("1" < "1.1" < "2" < "10")."""
    key: list[int] = []
    for chunk in str(node_id or "").split("."):
        try:
            key.append(int(chunk))
        except ValueError:
            key.append(10**9)
    return tuple(key)


def _ordered_sections(rows: list[BidDraftSection]) -> list[BidDraftSection]:
    # Never rely on SQL ORDER BY node_id: locale collations treat "." as
    # ignorable punctuation, so "10" sorts between "1" and "1.1". created_at
    # ties too — rows are bulk-inserted in one commit (2026-08-25 incident).
    return sorted(rows, key=lambda row: _node_sort_key(row.node_id))


async def _owned_task(task_id: str, current_user, db: DBSession) -> BidDraftTask:
    task = (
        await db.execute(select(BidDraftTask).where(BidDraftTask.id == task_id))
    ).scalar_one_or_none()
    if task is None or task.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="标书生成任务不存在或无权访问")
    return task


def _resolve_workspace_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(get_settings().workspace_path) / path
    return path


async def _dispatch_new_task(db: DBSession, task: BidDraftTask) -> BidDraftTask:
    from backend.services.task_lifecycle import add_task_dispatch, dispatch_task_outbox

    db.add(task)
    await db.flush()
    outbox = add_task_dispatch(db, task_kind="bid_draft", task_id=task.id)
    await db.flush()
    task.celery_task_id = outbox.celery_task_id
    await db.commit()
    await db.refresh(task)
    await dispatch_task_outbox(outbox.id)
    return task


@router.post("/tasks", response_model=BidDraftTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: BidDraftTaskCreate,
    db: DBSession,
    current_user: CurrentUser,
) -> BidDraftTask:
    """Create and enqueue one bid draft generation run."""
    project = (
        await db.execute(select(Project).where(Project.id == body.project_id))
    ).scalar_one_or_none()
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="项目不存在或无权访问")
    document = (
        await db.execute(select(Document).where(Document.id == body.tender_document_id))
    ).scalar_one_or_none()
    if document is None or document.project_id != project.id:
        raise HTTPException(status_code=404, detail="招标文件不存在或不属于该项目")
    if document.status != "parsed" or not document.parsed_markdown_path:
        raise HTTPException(status_code=409, detail="招标文件尚未解析完成，请稍后重试或重新上传")

    from backend.agent.bid_draft_agent import normalize_outline

    outline_value = None
    if body.outline:
        outline_value = normalize_outline([node.model_dump() for node in body.outline])
        if not outline_value:
            raise HTTPException(status_code=422, detail="章节大纲不能为空")
    analysis_value = body.analysis if isinstance(body.analysis, dict) and body.analysis else None
    options_value = body.options.model_dump(exclude_none=True) if body.options is not None else None

    from backend.services.task_lifecycle import authorize_billable_task_start
    from backend.services.sales import multiplier_for_task

    sales_config = await authorize_billable_task_start(
        db, user_id=current_user.id, operation_name="AI 标书生成"
    )
    task = BidDraftTask(
        user_id=current_user.id,
        project_id=project.id,
        tender_document_id=document.id,
        analysis_result=analysis_value,
        outline=outline_value,
        generation_options=options_value,
        status="pending",
        billing_multiplier=multiplier_for_task(sales_config, "bid_draft"),
        billing_status="pending",
    )
    return await _dispatch_new_task(db, task)


@router.post(
    "/tasks/{task_id}/sections/{node_id}/regenerate",
    response_model=BidDraftTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def regenerate_section(
    task_id: str,
    node_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> BidDraftTask:
    """Regenerate one outline node in a new task that reuses the stored outline."""
    task = await _owned_task(task_id, current_user, db)
    if task.status not in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="任务尚未结束，不能重生成章节")
    outline = task.outline if isinstance(task.outline, list) else []
    if not any(
        isinstance(node, dict) and node.get("node_id") == node_id for node in outline
    ):
        raise HTTPException(status_code=404, detail="章节不存在于该任务的大纲中")

    from backend.services.task_lifecycle import authorize_billable_task_start
    from backend.services.sales import multiplier_for_task

    sales_config = await authorize_billable_task_start(
        db, user_id=current_user.id, operation_name="AI 标书生成"
    )
    options = dict(task.generation_options or {})
    options["only_sections"] = [node_id]
    options.pop("outline_hint", None)
    new_task = BidDraftTask(
        user_id=current_user.id,
        project_id=task.project_id,
        tender_document_id=task.tender_document_id,
        analysis_result=task.analysis_result,
        outline=outline,
        generation_options=options,
        continue_of=task.id,
        status="pending",
        billing_multiplier=multiplier_for_task(sales_config, "bid_draft"),
        billing_status="pending",
    )
    return await _dispatch_new_task(db, new_task)


@router.get("/tasks/{task_id}", response_model=BidDraftTaskResponse)
async def get_task(task_id: str, db: DBSession, current_user: CurrentUser) -> BidDraftTask:
    return await _owned_task(task_id, current_user, db)


@router.get("/tasks/{task_id}/sections", response_model=list[BidDraftSectionResponse])
async def list_sections(
    task_id: str, db: DBSession, current_user: CurrentUser
) -> list[BidDraftSection]:
    task = await _owned_task(task_id, current_user, db)
    rows = _ordered_sections(
        list(
            (
                await db.execute(
                    select(BidDraftSection).where(BidDraftSection.task_id == task.id)
                )
            ).scalars().all()
        )
    )
    return list(rows)


@router.get("/tasks/{task_id}/sections/{node_id}", response_model=BidDraftSectionContentResponse)
async def get_section_content(
    task_id: str, node_id: str, db: DBSession, current_user: CurrentUser
) -> BidDraftSectionContentResponse:
    task = await _owned_task(task_id, current_user, db)
    row = (
        await db.execute(
            select(BidDraftSection).where(
                BidDraftSection.task_id == task.id,
                BidDraftSection.node_id == node_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    content = None
    if row.content_path:
        try:
            content = _resolve_workspace_path(row.content_path).read_text(
                encoding="utf-8", errors="replace"
            )[:200_000]
        except Exception:
            content = None
    return BidDraftSectionContentResponse(
        node_id=row.node_id,
        title=row.title,
        status=row.status,
        content=content,
        word_count=row.word_count,
    )


@router.get("/tasks/{task_id}/assembled", response_model=BidDraftAssembledResponse)
async def get_assembled_content(
    task_id: str, db: DBSession, current_user: CurrentUser
) -> BidDraftAssembledResponse:
    """Assembled markdown of all generated sections, in outline order."""
    task = await _owned_task(task_id, current_user, db)
    rows = _ordered_sections(
        list(
            (
                await db.execute(
                    select(BidDraftSection).where(BidDraftSection.task_id == task.id)
                )
            ).scalars().all()
        )
    )
    parts: list[str] = []
    generated = 0
    failed = 0
    total_words = 0
    for row in rows:
        if row.status == "failed":
            failed += 1
            continue
        if row.status != "generated" or not row.content_path:
            continue
        try:
            parts.append(
                _resolve_workspace_path(row.content_path).read_text(
                    encoding="utf-8", errors="replace"
                )
            )
            generated += 1
            total_words += int(row.word_count or 0)
        except Exception:
            failed += 1
    return BidDraftAssembledResponse(
        task_id=task.id,
        status=task.status,
        content="\n\n".join(parts) if parts else None,
        word_count=total_words,
        section_total=len(rows),
        section_generated=generated,
        section_failed=failed,
    )


@router.post("/tasks/{task_id}/cancel", response_model=BidDraftTaskResponse)
async def cancel_task(task_id: str, db: DBSession, current_user: CurrentUser) -> BidDraftTask:
    task = await _owned_task(task_id, current_user, db)
    if task.status in _TERMINAL_STATUSES:
        return task
    from backend.services.task_lifecycle import (
        cancel_pending_dispatch,
        enqueue_billing_settlement,
        finalize_task_usage,
    )

    cancelled_before_dispatch = await cancel_pending_dispatch(
        db, task_kind="bid_draft", task_id=task_id
    )
    if task.celery_task_id and not cancelled_before_dispatch:
        try:
            from backend.celery_app import celery_app

            celery_app.control.revoke(task.celery_task_id, terminate=False)
        except Exception:
            pass
    try:
        from backend.tasks.bid_draft_tasks import set_bid_draft_cancelled

        set_bid_draft_cancelled(task.id)
    except Exception:
        if task.celery_task_id and not cancelled_before_dispatch:
            from backend.celery_app import celery_app

            celery_app.control.revoke(task.celery_task_id, terminate=True)
    task.status = "cancelled"
    task.error_message = "用户取消了标书生成任务"
    task.completed_at = utc_now()
    task.billing_status = "pending"
    if cancelled_before_dispatch:
        task.usage_finalized_at = utc_now()
    await db.commit()
    await db.refresh(task)
    if cancelled_before_dispatch:
        await finalize_task_usage("bid_draft", task_id)
    else:
        from backend.config import get_settings

        enqueue_billing_settlement(
            "bid_draft",
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
    """Stream task progress; frontend uses fetch so Authorization is retained.

    与暗标检查一致：对内部、外部用户均推送完整事件（status/phase/
    section_started/section_completed/section_failed/result 等详细步骤），
    **不做** review 那类按 interior_user 的事件过滤——标书生成的完整时间线
    对所有用户可见是产品要求（doc/workspace/16-bid-generation.md）。
    后续如需收敛敏感信息，应收敛事件内容本身而不是按角色隐藏步骤。
    """
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
