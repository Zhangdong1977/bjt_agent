"""Duplicate-check project task API."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from backend.api.deps import CurrentUser, DBSession, get_token_claims, oauth2_scheme
from backend.config import get_settings
from backend.models import Document, DuplicatePairResult, Project, ReviewTask, TodoItem
from backend.schemas.duplicate_check import DuplicateResultsResponse, DuplicateTaskResponse
from backend.schemas.review import TodoItemResponse
from backend.services.duplicate_rule_loader import load_duplicate_rule, snapshot_duplicate_rule
from backend.utils.time_utils import utc_now

router = APIRouter(prefix="/projects/{project_id}/duplicate-check", tags=["Duplicate Check"])
settings = get_settings()


async def _project(project_id: str, current_user, db: DBSession) -> Project:
    item = (await db.execute(select(Project).where(
        Project.id == project_id, Project.user_id == current_user.id,
        Project.is_deleted.is_(False), Project.project_type == "duplicate",
    ))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="查重项目不存在或无权访问")
    return item


async def _task(project_id: str, task_id: str, current_user, db: DBSession) -> ReviewTask:
    await _project(project_id, current_user, db)
    item = (await db.execute(select(ReviewTask).where(
        ReviewTask.id == task_id, ReviewTask.project_id == project_id,
        ReviewTask.task_type == "duplicate",
    ))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="查重任务不存在")
    return item


async def _create_task(request: Request, project: Project, current_user, db: DBSession,
                       retry_pairs: list[list[str]] | None = None) -> ReviewTask:
    from backend.services.billing import ensure_wallet
    wallet = await ensure_wallet(db, current_user.id)
    if wallet.balance_wen <= 0:
        raise HTTPException(status_code=402, detail={
            "code": "INSUFFICIENT_BALANCE", "message": "余额不足，请先充值后再发起 AI 查重",
            "balance_wen": wallet.balance_wen,
        })
    active = await db.scalar(select(func.count(ReviewTask.id)).where(
        ReviewTask.project_id == project.id, ReviewTask.task_type == "duplicate",
        ReviewTask.status.in_(["pending", "running"]),
    ))
    if active:
        raise HTTPException(status_code=409, detail="该项目已有进行中的查重任务")

    docs = list((await db.execute(select(Document).where(
        Document.project_id == project.id, Document.doc_type == "duplicate_bid"
    ))).scalars().all())
    if not 2 <= len(docs) <= settings.duplicate_check_max_documents:
        raise HTTPException(status_code=400, detail="查重项目必须包含 2 至 5 份投标文件")
    unfinished = [d.original_filename for d in docs if d.status != "parsed" or not d.parsed_markdown_path]
    if unfinished:
        raise HTTPException(status_code=400, detail=f"以下文件尚未成功解析：{'、'.join(unfinished)}")
    valid_ids = {doc.id for doc in docs}
    if retry_pairs and any(a not in valid_ids or b not in valid_ids or a == b for a, b in retry_pairs):
        raise HTTPException(status_code=400, detail="失败重试的文档对已失效")

    rule = load_duplicate_rule(settings.duplicate_rule_path)
    token = await oauth2_scheme(request)
    concurrency = max(1, int(get_token_claims(token).get("concurrency", settings.max_sub_agent_concurrency)))
    task = ReviewTask(project_id=project.id, task_type="duplicate", status="pending",
                      max_concurrency=concurrency)
    db.add(task)
    await db.flush()
    snap = snapshot_duplicate_rule(
        rule, settings.workspace_path / current_user.id / project.id / "rules" / task.id
    )
    task.config_snapshot = {
        "rule": {"name": snap.name, "version": snap.config.version, "sha256": snap.sha256,
                 "source_path": snap.source_path, "snapshot_path": snap.snapshot_path},
        "retry_document_pairs": retry_pairs,
    }
    await db.commit()
    await db.refresh(task)

    from backend.tasks.duplicate_tasks import run_duplicate_check
    try:
        result = run_duplicate_check.delay(task.id)
    except Exception as exc:
        task.status, task.error_message, task.completed_at = (
            "failed", "任务队列暂不可用，请稍后重试", utc_now()
        )
        await db.commit()
        raise HTTPException(status_code=503, detail=task.error_message) from exc
    task.celery_task_id = result.id
    await db.commit()
    return task


@router.post("/tasks", response_model=DuplicateTaskResponse, status_code=201)
async def start_duplicate(request: Request, project_id: str, db: DBSession,
                          current_user: CurrentUser):
    return await _create_task(request, await _project(project_id, current_user, db), current_user, db)


@router.get("/tasks", response_model=list[DuplicateTaskResponse])
async def list_tasks(project_id: str, db: DBSession, current_user: CurrentUser):
    await _project(project_id, current_user, db)
    return list((await db.execute(select(ReviewTask).where(
        ReviewTask.project_id == project_id, ReviewTask.task_type == "duplicate"
    ).order_by(ReviewTask.created_at.desc()))).scalars().all())


@router.get("/tasks/{task_id}", response_model=DuplicateTaskResponse)
async def get_task(project_id: str, task_id: str, db: DBSession, current_user: CurrentUser):
    return await _task(project_id, task_id, current_user, db)


@router.post("/tasks/{task_id}/heartbeat")
async def heartbeat(project_id: str, task_id: str, db: DBSession, current_user: CurrentUser):
    item = await _task(project_id, task_id, current_user, db)
    if item.status in ("pending", "running"):
        item.last_heartbeat = utc_now()
        await db.commit()
    return {"status": item.status, "last_heartbeat": item.last_heartbeat}


@router.post("/tasks/{task_id}/cancel", response_model=DuplicateTaskResponse)
async def cancel(project_id: str, task_id: str, db: DBSession, current_user: CurrentUser):
    item = await _task(project_id, task_id, current_user, db)
    if item.status not in ("pending", "running"):
        return item
    from backend.tasks.review_tasks import set_task_cancelled
    set_task_cancelled(item.id)
    if item.celery_task_id:
        from backend.celery_app import celery_app
        celery_app.control.revoke(item.celery_task_id, terminate=False)
    item.status, item.completed_at = "cancelled", utc_now()
    await db.commit()
    return item


@router.get("/tasks/{task_id}/pairs", response_model=list[TodoItemResponse])
async def list_pairs(project_id: str, task_id: str, db: DBSession, current_user: CurrentUser):
    await _task(project_id, task_id, current_user, db)
    return list((await db.execute(select(TodoItem).where(
        TodoItem.session_id == task_id, TodoItem.todo_type == "duplicate_pair"
    ).order_by(TodoItem.created_at))).scalars().all())


@router.get("/tasks/{task_id}/results", response_model=DuplicateResultsResponse)
async def results(project_id: str, task_id: str, db: DBSession, current_user: CurrentUser):
    await _task(project_id, task_id, current_user, db)
    todos = list((await db.execute(select(TodoItem).where(
        TodoItem.session_id == task_id, TodoItem.todo_type == "duplicate_pair"
    ))).scalars().all())
    rows = list((await db.execute(select(DuplicatePairResult).where(
        DuplicatePairResult.task_id == task_id
    ).order_by(DuplicatePairResult.created_at))).scalars().all())
    doc_ids = (
        {todo.document_a_id for todo in todos if todo.document_a_id}
        | {todo.document_b_id for todo in todos if todo.document_b_id}
    )
    docs = list((await db.execute(select(Document).where(Document.id.in_(doc_ids)))).scalars().all()) if doc_ids else []
    names = {doc.id: doc.original_filename for doc in docs}
    pairs = [{
        "id": row.id, "todo_id": row.todo_id,
        "document_a_id": row.document_a_id, "document_b_id": row.document_b_id,
        "document_a_name": names.get(row.document_a_id), "document_b_name": names.get(row.document_b_id),
        "execution_mode": row.execution_mode, "conclusion": row.conclusion,
        "summary": row.summary, "suspicious_count": row.suspicious_count,
        "excluded_count": row.excluded_count, "matches": row.matches,
        "rule_name": row.rule_name, "rule_version": row.rule_version,
        "rule_hash": row.rule_hash, "created_at": row.created_at,
    } for row in rows]
    return {"summary": {
        "document_count": len(doc_ids), "pair_count": len(todos),
        "completed_pair_count": sum(todo.status == "completed" for todo in todos),
        "failed_pair_count": sum(todo.status == "failed" for todo in todos),
        "suspicious_pair_count": sum(row.conclusion == "suspicious_duplicate" for row in rows),
        "suspicious_item_count": sum(row.suspicious_count for row in rows),
    }, "pairs": pairs}


@router.get("/tasks/{task_id}/pairs/{todo_id}/report", response_class=PlainTextResponse)
async def report(project_id: str, task_id: str, todo_id: str, db: DBSession,
                 current_user: CurrentUser):
    await _task(project_id, task_id, current_user, db)
    row = (await db.execute(select(DuplicatePairResult).where(
        DuplicatePairResult.task_id == task_id, DuplicatePairResult.todo_id == todo_id
    ))).scalar_one_or_none()
    if not row or not row.report_path or not Path(row.report_path).is_file():
        raise HTTPException(status_code=404, detail="查重报告不存在")
    return Path(row.report_path).read_text(encoding="utf-8")


@router.post("/tasks/{task_id}/retry-failed", response_model=DuplicateTaskResponse, status_code=201)
async def retry_failed(request: Request, project_id: str, task_id: str, db: DBSession,
                       current_user: CurrentUser):
    project = await _project(project_id, current_user, db)
    await _task(project_id, task_id, current_user, db)
    failed = list((await db.execute(select(TodoItem).where(
        TodoItem.session_id == task_id, TodoItem.todo_type == "duplicate_pair",
        TodoItem.status == "failed",
    ))).scalars().all())
    if not failed:
        raise HTTPException(status_code=400, detail="没有可重试的失败文档对")
    return await _create_task(request, project, current_user, db,
                              retry_pairs=[[t.document_a_id, t.document_b_id] for t in failed])
