"""Review result sharing API.

Allows a project owner to share a single review task's results with other
logged-in users via a token-based link/QR code. Unlike normal review endpoints
(``/projects/{id}/review``), the read endpoints here require a valid account
login but do **not** check project ownership — anyone logged in holding a valid
token may view the shared snapshot. Only the token creator may disable it.
"""

import logging
from datetime import datetime
from pathlib import Path as FilePath

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from backend.api.deps import CurrentUser, DBSession
from backend.config import get_settings
from backend.models import Project, ReviewTask, ReviewResult, ReviewShareToken, TodoItem
from backend.schemas.share import (
    ShareTokenCreate,
    ShareTokenResponse,
    SharedTodoItemResponse,
    SharedReviewPayload,
)
from backend.schemas.review import ReviewResultResponse
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/share", tags=["Share"])
settings = get_settings()


async def _verify_my_project(
    project_id: str, current_user: CurrentUser, db: DBSession
) -> Project:
    """Only the project owner may create a share token for it.

    Unlike review.py's ``verify_project_ownership`` we do NOT grant interior
    users the right to mint share tokens for other people's projects — sharing
    is an owner action.
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project or project.user_id != current_user.id or project.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在或无权访问",
        )
    if project.project_type != "review":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="查重项目第一版暂不支持分享",
        )
    return project


async def _resolve_share(
    token_value: str, db: DBSession, *, require_active: bool = True
) -> ReviewShareToken:
    """Validate a share token and return the record.

    Raises 404 for unknown / disabled / expired tokens (404 instead of 403 to
    avoid leaking token existence).
    """
    result = await db.execute(
        select(ReviewShareToken).where(ReviewShareToken.token == token_value)
    )
    share = result.scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分享链接无效或已被撤销")
    if require_active and not share.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分享链接无效或已被撤销")
    if require_active and share.expires_at is not None and share.expires_at <= utc_now():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分享链接已过期")
    return share


async def _get_shared_project(share: ReviewShareToken, db: DBSession) -> Project:
    """Resolve the still-visible project behind a share token."""
    result = await db.execute(
        select(Project).where(
            Project.id == share.project_id,
            Project.project_type == "review",
            Project.is_deleted.is_(False),
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="分享的项目已不存在",
        )
    return project


@router.post(
    "/projects/{project_id}/tasks/{task_id}",
    response_model=ShareTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_share_token(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
    payload: ShareTokenCreate | None = None,
) -> ShareTokenResponse:
    """Create (or reuse) a share token for a review task.

    Only the project owner may mint a token. Reusing an existing active token
    for the same task keeps the share URL stable across repeated "分享" clicks.
    """
    import secrets

    await _verify_my_project(project_id, current_user, db)

    # Validate that the task belongs to this project.
    # Serialize token issuance for the same task.  Together with the partial
    # unique index this prevents double-clicks/concurrent requests from
    # creating two independently revocable "current" links.
    task_result = await db.execute(
        select(ReviewTask)
        .where(
            ReviewTask.id == task_id,
            ReviewTask.project_id == project_id,
            ReviewTask.task_type == "review",
        )
        .with_for_update()
    )
    task = task_result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="审查任务不存在或已被删除",
        )

    expires_at = payload.expires_at if payload else None

    # Reuse existing active token for the same task (created by same user) to
    # keep the share URL stable; only override expiry when a new one is given.
    existing_result = await db.execute(
        select(ReviewShareToken).where(
            ReviewShareToken.task_id == task_id,
            ReviewShareToken.created_by_user_id == current_user.id,
            ReviewShareToken.is_active.is_(True),
        ).order_by(ReviewShareToken.created_at.desc()).limit(1)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        if existing.expires_at is None or existing.expires_at > utc_now():
            if expires_at is not None:
                existing.expires_at = expires_at
            await db.flush()
            return ShareTokenResponse(
                token=existing.token,
                share_url=f"/shared/{existing.token}",
                project_id=existing.project_id,
                task_id=existing.task_id,
                expires_at=existing.expires_at,
                created_at=existing.created_at,
            )

        # An expired row may still be marked active.  Retire it before issuing
        # a replacement so the partial unique index remains satisfied.
        existing.is_active = False
        await db.flush()

    share = ReviewShareToken(
        project_id=project_id,
        task_id=task_id,
        token=secrets.token_urlsafe(32),
        created_by_user_id=current_user.id,
        expires_at=expires_at,
        is_active=True,
    )
    db.add(share)
    await db.flush()

    return ShareTokenResponse(
        token=share.token,
        share_url=f"/shared/{share.token}",
        project_id=share.project_id,
        task_id=share.task_id,
        expires_at=share.expires_at,
        created_at=share.created_at,
    )


@router.get("/{token}", response_model=SharedReviewPayload)
async def get_shared_review(
    token: str,
    db: DBSession,
    current_user: CurrentUser,  # noqa: ARG001  # login required, ownership NOT checked
) -> SharedReviewPayload:
    """Fetch the read-only snapshot of a shared review task.

    Any logged-in user holding a valid token may read it — project ownership is
    intentionally NOT verified here.
    """
    share = await _resolve_share(token, db)

    task_result = await db.execute(
        select(ReviewTask).where(
            ReviewTask.id == share.task_id,
            ReviewTask.project_id == share.project_id,
            ReviewTask.task_type == "review",
        )
    )
    task = task_result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="分享的审查任务已不存在",
        )

    project = await _get_shared_project(share, db)

    findings_result = await db.execute(
        select(ReviewResult)
        .where(ReviewResult.task_id == share.task_id)
        .order_by(ReviewResult.severity.asc(), ReviewResult.created_at.asc())
    )
    findings = findings_result.scalars().all()

    todos_result = await db.execute(
        select(TodoItem)
        .where(TodoItem.session_id == share.task_id)
        .order_by(TodoItem.created_at.asc())
    )
    todos = todos_result.scalars().all()

    return SharedReviewPayload(
        project_id=share.project_id,
        task_id=share.task_id,
        project_name=project.name if project else None,
        findings=[ReviewResultResponse.model_validate(f) for f in findings],
        todos=[SharedTodoItemResponse.model_validate(t) for t in todos],
    )


@router.get("/{token}/report/{todo_id}")
async def get_shared_todo_report(
    token: str,
    todo_id: str,
    db: DBSession,
    current_user: CurrentUser,  # noqa: ARG001  # login required, ownership NOT checked
) -> PlainTextResponse:
    """Fetch the markdown report for a specific todo within a shared task.

    Mirrors ``GET /projects/{id}/review/tasks/{task_id}/todos/{todo_id}/report``
    but authenticates via the share token instead of project ownership. The
    report file is resolved under the project OWNER's workspace.
    """
    share = await _resolve_share(token, db)

    result = await db.execute(
        select(TodoItem).where(
            TodoItem.id == todo_id,
            TodoItem.session_id == share.task_id,
            TodoItem.project_id == share.project_id,
        )
    )
    todo = result.scalar_one_or_none()
    if not todo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="检查项不存在或已被删除")

    project = await _get_shared_project(share, db)
    report_path = (todo.result or {}).get("report_path")
    workspace_dir = (
        settings.workspace_path / str(project.user_id) / share.project_id
    ).resolve()

    # Fallback: scan the OWNER's workspace for review_*.md files.
    if not report_path:
        if workspace_dir.exists():
            review_files = sorted(workspace_dir.glob("review_*.md"))
            if review_files:
                report_path = str(review_files[-1])

    if not report_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="审查报告尚未生成")

    report_file = FilePath(report_path).resolve()
    try:
        report_file.relative_to(workspace_dir)
    except ValueError:
        logger.warning(
            "Rejected shared report path outside project workspace: project_id=%s todo_id=%s",
            share.project_id,
            todo_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="审查报告文件不存在，请重新生成",
        )
    if not report_file.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="审查报告文件不存在，请重新生成")

    content = report_file.read_text(encoding="utf-8")
    return PlainTextResponse(content=content, media_type="text/markdown")


@router.delete("/{token}")
async def revoke_share_token(
    token: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """Disable a share token. Only its creator may revoke it."""
    share = await _resolve_share(token, db, require_active=False)
    if share.created_by_user_id != current_user.id:
        # Do not leak token existence to non-owners.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分享链接无效或已被撤销")
    share.is_active = False
    return {"msg": "分享链接已撤销"}
