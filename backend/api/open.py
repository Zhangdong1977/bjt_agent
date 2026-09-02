"""Open API for third-party skill clients (WorkBuddy etc.).

面向第三方智能体技能包（WorkBuddy skill / bjt.py 客户端）的开放通道：
``X-Api-Key`` 鉴权 → 绑定 bjt-agent 用户 → 复用现有的解析、审查/查重
执行（outbox 派发）、计费结算与 AI 用量审计链路。

契约文档：``sales/skill/bjt-bid-review/references/api.md``（v0）。
设计要点（相对 Web 通道的差异）：
- 鉴权是 API key（sha256 落库），不是 JWT；无浏览器会话。
- 任务 ``client_channel='api'``：agent 侧豁免前端心跳超时取消。
- 隐式项目/文档 ``source='api'``：与 Web 草稿配额互相隔离，Web 历史列表隐藏。
- 提交类接口支持 ``Idempotency-Key``（24h 内重复提交返回同一 task_id）。
- 错误体沿用 house style：``{"detail": {"code": ..., "message": ...}}``。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.documents import _save_upload_file, _validate_upload_file
from backend.config import get_settings
from backend.middleware.rate_limit import limiter
from backend.models import (
    ApiKey,
    Document,
    DuplicateResult,
    Project,
    ReviewResult,
    ReviewShareToken,
    ReviewTask,
    TodoItem,
    User,
    get_db_session,
)
from backend.schemas.open import (
    DocumentStatusResponse,
    DocumentUploadResponse,
    DuplicateSubmitRequest,
    MeResponse,
    ProgressInfo,
    ReviewSubmitRequest,
    TaskListItem,
    TaskListResponse,
    TaskStatusResponse,
    TaskSubmitResponse,
)
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1/open", tags=["OpenAPI"])

OPEN_DOC_TYPES = ("tender", "bid", "duplicate_left", "duplicate_right")
SEVERITY_TO_RISK = {"critical": "high", "major": "review", "minor": "tip"}
OPEN_SHARE_TTL_DAYS = 7


def _err(code: str, message: str, http_status: int) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code, "message": message})


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def get_api_principal(
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> tuple[User, ApiKey]:
    """Authenticate an open-channel request via X-Api-Key → (user, api_key)."""

    if not settings.open_api_enabled:
        # 与竞品同款语义：总开关关闭时整层 404，不暴露端点存在性
        raise _err("not_found", "接口不存在", status.HTTP_404_NOT_FOUND)
    if not x_api_key:
        raise _err(
            "missing_credentials",
            "缺少 X-Api-Key 请求头，请先配置 bjt-agent API Key",
            status.HTTP_401_UNAUTHORIZED,
        )
    key_row = (
        await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(x_api_key)))
    ).scalar_one_or_none()
    if key_row is None:
        raise _err("invalid_credentials", "API Key 无效", status.HTTP_401_UNAUTHORIZED)
    if key_row.revoked_at is not None:
        raise _err("key_revoked", "API Key 已被吊销，请重新生成", status.HTTP_403_FORBIDDEN)
    user = (
        await db.execute(select(User).where(User.id == key_row.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise _err("invalid_credentials", "API Key 无效", status.HTTP_401_UNAUTHORIZED)

    # 审计：key 最后使用时间。独立小事务，避免污染请求事务。
    try:
        from backend.models import async_session_factory

        async with async_session_factory() as audit_db:
            await audit_db.execute(
                select(ApiKey).where(ApiKey.id == key_row.id)
            )
            key_row.last_used_at = utc_now()
            await audit_db.commit()
    except Exception:
        logger.debug("[open-api] last_used_at update failed for key=%s", key_row.id)

    return user, key_row


ApiPrincipal = Annotated[tuple[User, ApiKey], Depends(get_api_principal)]
DBSession = Annotated[AsyncSession, Depends(get_db_session)]


def _api_key_limit_key(request: Request) -> str:
    """Rate-limit key: hash of the API key (falls back to client IP)."""
    raw = request.headers.get("X-Api-Key")
    if raw:
        return "apikey:" + hash_api_key(raw)
    return "ip:" + (request.client.host if request.client else "unknown")


# ---------------------------------------------------------------------------
# Idempotency (Redis, best-effort)
# ---------------------------------------------------------------------------

def _idem_redis():
    if not settings.redis_url:
        return None
    try:
        import redis

        return redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=3)
    except Exception:
        return None


def _idem_cache_key(user_id: str, idempotency_key: str) -> str:
    return "open:idem:%s:%s" % (user_id, hashlib.sha256(idempotency_key.encode()).hexdigest())


def _idem_lookup(user_id: str, idempotency_key: str | None) -> str | None:
    if not idempotency_key:
        return None
    r = _idem_redis()
    if r is None:
        return None
    try:
        cached = r.get(_idem_cache_key(user_id, idempotency_key))
        if isinstance(cached, bytes):
            cached = cached.decode()
        return cached
    except Exception:
        return None


def _idem_store(user_id: str, idempotency_key: str | None, task_id: str) -> None:
    if not idempotency_key:
        return
    r = _idem_redis()
    if r is None:
        return
    try:
        r.setex(_idem_cache_key(user_id, idempotency_key), 24 * 3600, task_id)
    except Exception:
        logger.debug("[open-api] idempotency store failed for task=%s", task_id)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _my_open_document(db: AsyncSession, *, user: User, document_id: str) -> Document:
    document = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if (
        document is None
        or document.owner_user_id != user.id
        or document.project_id is not None
    ):
        raise _err("document_not_found", "文档不存在、不属于当前账号或已被任务使用", status.HTTP_404_NOT_FOUND)
    return document


def _require_parsed(document: Document, label: str) -> None:
    if document.status != "parsed":
        raise _err(
            "validation_error",
            f"{label}尚未解析完成（当前状态 {document.status}），请稍后再试",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


async def _create_implicit_project(
    db: AsyncSession, *, user: User, name: str, project_type: str
) -> Project:
    project = Project(
        user_id=user.id,
        name=name[:255],
        project_type=project_type,
        duplicate_mode="pair" if project_type == "duplicate" else "pair",
        status="draft",
        source="api",
    )
    db.add(project)
    await db.flush()
    return project


async def _submit_open_task(
    db: AsyncSession,
    *,
    user: User,
    api_key: ApiKey,
    project: Project,
    task_type: str,
    duplicate_mode: str | None = None,
    duplicate_feature_snapshot: dict | None = None,
) -> ReviewTask:
    """Authorize billing with the per-key quota, then create + dispatch a task."""

    from backend.services.sales import multiplier_for_task
    from backend.services.task_lifecycle import (
        add_task_dispatch,
        authorize_billable_task_start,
        dispatch_task_outbox,
    )

    operation = "AI 查重" if task_type == "duplicate" else "AI 检查"
    sales_config = await authorize_billable_task_start(
        db,
        user_id=user.id,
        operation_name=operation,
        max_active_tasks=api_key.max_active_tasks,
    )

    task = ReviewTask(
        project_id=project.id,
        task_type=task_type,
        duplicate_mode=duplicate_mode or "pair",
        duplicate_feature_snapshot=duplicate_feature_snapshot,
        duplicate_algorithm_version=(
            duplicate_feature_snapshot.get("algorithm_version") if duplicate_feature_snapshot else None
        ),
        status="pending",
        max_concurrency=max(1, int(settings.max_sub_agent_concurrency)),
        billing_multiplier=multiplier_for_task(sales_config, task_type),
        billing_status="pending",
        client_channel="api",
    )
    db.add(task)
    await db.flush()
    outbox = add_task_dispatch(db, task_kind=task_type, task_id=task.id)
    await db.flush()
    task.celery_task_id = outbox.celery_task_id
    await db.commit()
    await db.refresh(task)
    # 机会式立即投递；失败的 outbox 由 beat 兜底重投
    await dispatch_task_outbox(outbox.id)
    return task


async def _my_open_task(db: AsyncSession, *, user: User, task_id: str) -> ReviewTask:
    task = (
        await db.execute(
            select(ReviewTask)
            .join(Project, Project.id == ReviewTask.project_id)
            .where(
                ReviewTask.id == task_id,
                Project.user_id == user.id,
                ReviewTask.task_type.in_(("review", "duplicate")),
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise _err("task_not_found", "任务不存在或无权访问", status.HTTP_404_NOT_FOUND)
    return task


def _task_progress(task: ReviewTask, done: int | None, total: int | None) -> ProgressInfo | None:
    if task.status == "pending":
        return ProgressInfo(percent=0, stage="queued", stage_label="排队中")
    if task.status == "running":
        if total:
            return ProgressInfo(
                percent=int(done * 100 / total),
                stage="check",
                stage_label="AI 检查执行中",
                current_step=done,
                total_steps=total,
                message=f"已完成 {done}/{total} 个检查项",
            )
        return ProgressInfo(percent=None, stage="check", stage_label="AI 检查执行中")
    if task.status == "completed":
        return ProgressInfo(percent=100, stage="done", stage_label="已完成")
    return None


def _task_status_payload(db_task: ReviewTask, progress: ProgressInfo | None) -> TaskStatusResponse:
    error = None
    if db_task.status == "failed":
        error = {"code": "task_failed", "message": db_task.error_message or "任务执行失败"}
    elif db_task.status == "cancelled":
        error = {"code": "canceled", "message": "任务已取消"}
    return TaskStatusResponse(
        task_id=db_task.id,
        service=db_task.task_type,
        status=db_task.status,
        billing_status=getattr(db_task, "billing_status", None),
        progress=progress,
        error=error,
        created_at=db_task.created_at.isoformat() if db_task.created_at else None,
        updated_at=db_task.updated_at.isoformat() if db_task.updated_at else None,
    )


async def _mint_share_url(
    db: AsyncSession, *, task: ReviewTask, user: User
) -> str | None:
    """Mint (or reuse) a share token and return an absolute report URL.

    Only review tasks have a web share view; duplicate tasks get no report URL
    in v0. Reuse mirrors share.py: at most one active token per (task, creator).
    """
    if task.task_type != "review":
        return None
    existing = (
        await db.execute(
            select(ReviewShareToken).where(
                ReviewShareToken.task_id == task.id,
                ReviewShareToken.created_by_user_id == user.id,
                ReviewShareToken.is_active.is_(True),
            ).order_by(ReviewShareToken.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None and (
        existing.expires_at is None or existing.expires_at > utc_now()
    ):
        token_value = existing.token
    else:
        if existing is not None:
            existing.is_active = False
            await db.flush()
        share = ReviewShareToken(
            project_id=task.project_id,
            task_id=task.id,
            token=secrets.token_urlsafe(32),
            created_by_user_id=user.id,
            expires_at=utc_now() + timedelta(days=OPEN_SHARE_TTL_DAYS),
            is_active=True,
        )
        db.add(share)
        await db.flush()
        token_value = share.token
    return f"{settings.public_base_url.rstrip('/')}/shared/{token_value}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/me", response_model=MeResponse)
async def get_me(principal: ApiPrincipal, db: DBSession) -> MeResponse:
    """Connectivity check: wallet balance and per-key limits."""
    user, api_key = principal
    from backend.services.billing import ensure_wallet
    from backend.services.sales import decimal_value, expire_user_lots
    from backend.services.task_lifecycle import count_unsettled_tasks

    wallet = await ensure_wallet(db, user.id)
    await expire_user_lots(db, wallet)
    recharge = decimal_value(wallet.recharge_balance_points)
    gift = decimal_value(wallet.gift_balance_points)
    running = await count_unsettled_tasks(db, user_id=user.id)
    return MeResponse(
        balance_points=float(recharge + gift),
        recharge_points=float(recharge),
        gift_points=float(gift),
        limits={
            "rate_per_min": settings.open_api_rate_per_minute,
            "max_active_tasks": max(1, int(api_key.max_active_tasks)),
            "running_tasks": running,
        },
    )


@router.post(
    "/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(f"{settings.open_api_rate_per_minute}/minute", key_func=_api_key_limit_key)
async def upload_document(
    request: Request,
    principal: ApiPrincipal,
    db: DBSession,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
) -> DocumentUploadResponse:
    """Upload a document for parsing (multipart only; URL fetch stays client-side).

    Documents live unattached until a task submission moves them into an
    implicit per-task project. They do NOT count toward the web draft quotas.
    """
    user, _api_key = principal
    if doc_type not in OPEN_DOC_TYPES:
        raise _err(
            "validation_error",
            f"doc_type 仅支持 {'/'.join(OPEN_DOC_TYPES)}",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    unattached = (
        await db.execute(
            select(Document).where(
                Document.owner_user_id == user.id,
                Document.project_id.is_(None),
                Document.source == "api",
            )
        )
    ).scalars()
    if len(unattached.all()) >= settings.open_api_max_unattached_documents:
        raise _err(
            "validation_error",
            f"未使用的上传文档数量已达上限（{settings.open_api_max_unattached_documents}），请先提交任务",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    _validate_upload_file(file)
    doc_dir = settings.workspace_path / str(user.id) / "_api" / doc_type
    file_path = await _save_upload_file(file, doc_dir)

    document = Document(
        project_id=None,
        owner_user_id=user.id,
        doc_type=doc_type,
        original_filename=file.filename,
        file_path=file_path,
        status="pending",
        source="api",
    )
    db.add(document)
    await db.flush()
    await db.commit()

    from backend.tasks.document_parser import parse_document

    parse_document.delay(document.id)
    logger.info("[open-api] upload: user=%s doc=%s type=%s", user.id, document.id, doc_type)
    return DocumentUploadResponse(
        document_id=document.id, doc_type=doc_type, status=document.status
    )


@router.get("/documents/{document_id}", response_model=DocumentStatusResponse)
async def get_document_status(
    document_id: str, principal: ApiPrincipal, db: DBSession
) -> DocumentStatusResponse:
    user, _ = principal
    document = (
        await db.execute(
            select(Document).where(
                Document.id == document_id, Document.owner_user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise _err("document_not_found", "文档不存在或无权访问", status.HTTP_404_NOT_FOUND)
    return DocumentStatusResponse(
        document_id=document.id,
        doc_type=document.doc_type,
        status=document.status,
        pages=document.page_count,
        word_count=document.word_count,
        error=document.parse_error,
    )


@router.post("/review", response_model=TaskSubmitResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute", key_func=_api_key_limit_key)
async def submit_review(
    request: Request,
    principal: ApiPrincipal,
    db: DBSession,
    payload: ReviewSubmitRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskSubmitResponse:
    """Submit a bid review task (tender + bid documents must be parsed)."""
    user, api_key = principal

    cached_task_id = _idem_lookup(user.id, idempotency_key)
    if cached_task_id:
        existing = (
            await db.execute(select(ReviewTask).where(ReviewTask.id == cached_task_id))
        ).scalar_one_or_none()
        if existing is not None:
            return TaskSubmitResponse(task_id=existing.id, service="review")

    if len(payload.tender_document_ids) > settings.review_doc_role_limit or (
        len(payload.bid_document_ids) > settings.review_doc_role_limit
    ):
        raise _err(
            "validation_error",
            f"单类文档数量超过上限（{settings.review_doc_role_limit}）",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if set(payload.tender_document_ids) & set(payload.bid_document_ids):
        raise _err("validation_error", "招标与投标文档列表存在重复", status.HTTP_422_UNPROCESSABLE_ENTITY)

    documents: list[Document] = []
    names: list[str] = []
    for document_id in [*payload.tender_document_ids, *payload.bid_document_ids]:
        document = await _my_open_document(db, user=user, document_id=document_id)
        if document.doc_type not in ("tender", "bid"):
            raise _err(
                "validation_error",
                f"文档 {document.original_filename} 的类型（{document.doc_type}）不适用于标书审查",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        _require_parsed(document, f"文档 {document.original_filename} ")
        documents.append(document)
        names.append(document.original_filename)

    project = await _create_implicit_project(
        db, user=user, name=f"API · {names[0]}", project_type="review"
    )
    for document in documents:
        document.project_id = project.id

    task = await _submit_open_task(
        db, user=user, api_key=api_key, project=project, task_type="review"
    )
    _idem_store(user.id, idempotency_key, task.id)
    logger.info(
        "[open-api] review submitted: user=%s project=%s task=%s", user.id, project.id, task.id
    )
    return TaskSubmitResponse(task_id=task.id, service="review")


@router.post(
    "/duplicate-check", response_model=TaskSubmitResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("20/minute", key_func=_api_key_limit_key)
async def submit_duplicate_check(
    request: Request,
    principal: ApiPrincipal,
    db: DBSession,
    payload: DuplicateSubmitRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskSubmitResponse:
    """Submit a pair duplicate-check task (two distinct parsed bid documents)."""
    import asyncio

    user, api_key = principal

    cached_task_id = _idem_lookup(user.id, idempotency_key)
    if cached_task_id:
        existing = (
            await db.execute(select(ReviewTask).where(ReviewTask.id == cached_task_id))
        ).scalar_one_or_none()
        if existing is not None:
            return TaskSubmitResponse(task_id=existing.id, service="duplicate")

    if payload.left_document_id == payload.right_document_id:
        raise _err(
            "identical_documents",
            "两份文档 ID 相同：查重需要两份不同的投标文件",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    left = await _my_open_document(db, user=user, document_id=payload.left_document_id)
    right = await _my_open_document(db, user=user, document_id=payload.right_document_id)
    for document, label in ((left, "A 方"), (right, "B 方")):
        if document.doc_type not in ("duplicate_left", "duplicate_right"):
            raise _err(
                "validation_error",
                f"{label}文档类型（{document.doc_type}）不适用于查重，"
                "应为 duplicate_left / duplicate_right",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        _require_parsed(document, f"{label}文档 ")

    from pathlib import Path as _Path

    from backend.services.duplicate_hash import find_identical_content_hash

    identical = await asyncio.to_thread(
        find_identical_content_hash,
        left.file_path,
        right.file_path,
        left.parsed_markdown_path or left.parsed_html_path,
        right.parsed_markdown_path or right.parsed_html_path,
    )
    if identical:
        basis, _digest = identical
        basis_text = "原始上传文件" if basis == "original" else "解析内容"
        raise _err(
            "identical_documents",
            f"两份技术投标文件的{basis_text}内容完全相同，无需发起 AI 查重",
            status.HTTP_400_BAD_REQUEST,
        )

    from backend.services.duplicate_runtime import build_duplicate_feature_snapshot

    project = await _create_implicit_project(
        db, user=user, name=f"API · 查重 {left.original_filename}", project_type="duplicate"
    )
    left.project_id = project.id
    right.project_id = project.id

    snapshot = build_duplicate_feature_snapshot(settings)
    task = await _submit_open_task(
        db,
        user=user,
        api_key=api_key,
        project=project,
        task_type="duplicate",
        duplicate_mode="pair",
        duplicate_feature_snapshot=snapshot,
    )
    _idem_store(user.id, idempotency_key, task.id)
    logger.info(
        "[open-api] duplicate submitted: user=%s project=%s task=%s", user.id, project.id, task.id
    )
    return TaskSubmitResponse(task_id=task.id, service="duplicate")


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    principal: ApiPrincipal,
    db: DBSession,
    page: int = 1,
) -> TaskListResponse:
    user, _ = principal
    if page < 1:
        page = 1
    result = await db.execute(
        select(ReviewTask, Project.name)
        .join(Project, Project.id == ReviewTask.project_id)
        .where(
            Project.user_id == user.id,
            ReviewTask.task_type.in_(("review", "duplicate")),
        )
        .order_by(ReviewTask.created_at.desc())
        .offset((page - 1) * 20)
        .limit(20)
    )
    rows = result.all()
    return TaskListResponse(
        tasks=[
            TaskListItem(
                task_id=task.id,
                service=task.task_type,
                status=task.status,
                title=project_name,
                created_at=task.created_at.isoformat() if task.created_at else None,
            )
            for task, project_name in rows
        ]
    )


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str, principal: ApiPrincipal, db: DBSession) -> TaskStatusResponse:
    user, _ = principal
    task = await _my_open_task(db, user=user, task_id=task_id)

    done = total = None
    if task.task_type == "review":
        todo_rows = (
            await db.execute(select(TodoItem).where(TodoItem.session_id == task.id))
        ).scalars().all()
        total = len(todo_rows)
        done = sum(1 for t in todo_rows if t.status in ("completed", "failed")) if total else None
    return _task_status_payload(task, _task_progress(task, done, total))


@router.get("/tasks/{task_id}/result")
async def get_task_result(task_id: str, principal: ApiPrincipal, db: DBSession) -> dict:
    user, _ = principal
    task = await _my_open_task(db, user=user, task_id=task_id)
    if task.status != "completed":
        raise _err(
            "invalid_job_state",
            f"任务尚未成功完成（当前状态 {task.status}）",
            status.HTTP_409_CONFLICT,
        )

    if task.task_type == "review":
        findings = (
            await db.execute(
                select(ReviewResult)
                .where(ReviewResult.task_id == task.id)
                .order_by(ReviewResult.severity.asc(), ReviewResult.created_at.asc())
            )
        ).scalars().all()
        non_compliant = [f for f in findings if not f.is_compliant]
        counts = {"high": 0, "review": 0, "tip": 0}
        for finding in non_compliant:
            counts[SEVERITY_TO_RISK.get(finding.severity, "tip")] += 1
        overall = task.overall_report or {}
        conclusion = (overall.get("summary") or {}).get("conclusion")
        issues = [
            {
                "id": finding.id,
                "risk_level": SEVERITY_TO_RISK.get(finding.severity, "tip"),
                "title": finding.check_item_name or finding.requirement_key,
                "description": finding.explanation,
                "tender_evidence": finding.requirement_content,
                "bid_evidence": finding.bid_content,
                "suggestion": finding.suggestion,
                "location_page": finding.location_page,
                "rule_doc_name": finding.rule_doc_name,
                "is_compliant": finding.is_compliant,
            }
            for finding in findings
        ]
        report_url = await _mint_share_url(db, task=task, user=user)
        await db.commit()
        return {
            "service": "review",
            "task_id": task.id,
            "report_url": report_url,
            "result": {
                "summary": {
                    "conclusion": conclusion,
                    "high_count": counts["high"],
                    "review_count": counts["review"],
                    "tip_count": counts["tip"],
                    "finding_count": len(findings),
                },
                "issues": issues,
            },
        }

    duplicate_rows = (
        await db.execute(
            select(DuplicateResult).where(DuplicateResult.task_id == task.id)
        )
    ).scalars().all()
    evidences = [
        {
            "rule_doc_name": row.rule_doc_name,
            "check_item_name": row.check_item_name,
            "verdict": row.verdict,
            "similarity_score": float(row.similarity_score) if row.similarity_score is not None else None,
            "left_location": row.left_location,
            "left_excerpt": row.left_excerpt,
            "right_location": row.right_location,
            "right_excerpt": row.right_excerpt,
        }
        for row in duplicate_rows
    ]
    first = duplicate_rows[0] if duplicate_rows else None
    return {
        "service": "duplicate",
        "task_id": task.id,
        "report_url": None,
        "result": {
            "verdict": first.verdict if first else "unknown",
            "similarity_score": (
                float(first.similarity_score) if first and first.similarity_score is not None else None
            ),
            "confidence": (
                float(first.confidence) if first and first.confidence is not None else None
            ),
            "channel_scores": first.channel_scores if first else None,
            "explanation": first.explanation if first else None,
            "suggestion": first.suggestion if first else None,
            "evidences": evidences,
        },
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, principal: ApiPrincipal, db: DBSession) -> dict:
    user, _ = principal
    task = await _my_open_task(db, user=user, task_id=task_id)
    if task.status not in ("pending", "running"):
        raise _err("invalid_job_state", "当前任务状态不可取消", status.HTTP_409_CONFLICT)

    from backend.services.task_lifecycle import (
        cancel_pending_dispatch,
        enqueue_billing_settlement,
        finalize_task_usage,
    )

    cancelled_before_dispatch = await cancel_pending_dispatch(
        db, task_kind=task.task_type, task_id=task.id
    )
    if task.celery_task_id and not cancelled_before_dispatch:
        from backend.celery_app import celery_app

        try:
            celery_app.control.revoke(task.celery_task_id, terminate=False)
        except Exception:
            pass

    from backend.tasks.review_tasks import set_task_cancelled

    try:
        set_task_cancelled(task.id)
    except Exception:
        logger.exception("[open-api] redis cancel flag failed: task=%s", task.id)

    task.status = "cancelled"
    task.completed_at = utc_now()
    task.billing_status = "pending"
    if cancelled_before_dispatch:
        task.usage_finalized_at = task.completed_at
    await db.commit()
    if cancelled_before_dispatch:
        await finalize_task_usage(task.task_type, task.id)
    else:
        enqueue_billing_settlement(
            task.task_type,
            task.id,
            countdown=settings.billing_orphan_finalize_grace_seconds,
        )
    return {"task_id": task.id, "status": "cancelled", "message": "已请求取消（注意：取消的任务也可能产生费用）"}
