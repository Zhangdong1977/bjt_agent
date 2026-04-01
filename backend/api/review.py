"""Review API routes."""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from backend.api.deps import DBSession, CurrentUser
from backend.models import Project, ReviewTask, ReviewResult, AgentStep, ProjectReviewResult
from backend.schemas.review import (
    ReviewResponse,
    ReviewResultResponse,
    ReviewTaskResponse,
    ReviewTaskListItem,
    AgentStepResponse,
)
from backend.services.sse_service import sse_manager

router = APIRouter(prefix="/projects/{project_id}/review", tags=["Review"])


async def verify_project_ownership(project_id: str, user_id: str, db: DBSession) -> Project:
    """Verify that the project exists and belongs to the user."""
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


@router.get("/tasks", response_model=list[ReviewTaskListItem])
async def list_review_tasks(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> list[ReviewTaskListItem]:
    """List all review tasks for the project (newest first)."""
    await verify_project_ownership(project_id, current_user.id, db)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.project_id == project_id)
        .order_by(ReviewTask.created_at.desc())
    )
    tasks = result.scalars().all()
    return tasks


@router.post("", response_model=ReviewTaskResponse, status_code=status.HTTP_201_CREATED)
async def start_review(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ReviewTask:
    """Start a new review task for the project."""
    await verify_project_ownership(project_id, current_user.id, db)

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
        existing_task.error_message = "Task cancelled - stale task from previous crashed worker"
        existing_task.completed_at = datetime.utcnow()
    if existing_tasks:
        await db.flush()

    # Create new review task
    task = ReviewTask(
        project_id=project_id,
        status="pending",
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    # Trigger the review task via Celery
    from backend.tasks.review_tasks import run_review
    celery_result = run_review.delay(task.id)
    task.celery_task_id = celery_result.id
    await db.flush()

    return task


@router.get("", response_model=ReviewResponse)
async def get_review_results(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ReviewResponse:
    """Get the merged review results for the project.

    Returns all merged non-compliant findings across all historical review tasks.
    """
    await verify_project_ownership(project_id, current_user.id, db)

    # Get all merged results for this project
    result = await db.execute(
        select(ProjectReviewResult)
        .where(ProjectReviewResult.project_id == project_id)
        .order_by(
            ProjectReviewResult.severity.asc(),  # critical first
            ProjectReviewResult.created_at.asc(),
        )
    )
    findings = result.scalars().all()
    logger.info(f"[get_review_results] project_id={project_id}, findings_count={len(findings)}")

    # Calculate summary
    summary = {
        "total_requirements": len(findings),
        "compliant": sum(1 for f in findings if f.is_compliant),
        "non_compliant": sum(1 for f in findings if not f.is_compliant),
        "critical": sum(1 for f in findings if f.severity == "critical" and not f.is_compliant),
        "major": sum(1 for f in findings if f.severity == "major" and not f.is_compliant),
        "minor": sum(1 for f in findings if f.severity == "minor" and not f.is_compliant),
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
    await verify_project_ownership(project_id, current_user.id, db)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.id == task_id, ReviewTask.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review task not found",
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
    await verify_project_ownership(project_id, current_user.id, db)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.id == task_id, ReviewTask.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review task not found",
        )

    if task.status not in ["pending", "running"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task cannot be cancelled",
        )

    task.status = "cancelled"
    await db.flush()
    await db.refresh(task)
    return task


@router.get("/tasks/{task_id}/steps", response_model=list[AgentStepResponse])
async def get_review_task_steps(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> list[AgentStep]:
    """Get all steps for a review task (for timeline display)."""
    await verify_project_ownership(project_id, current_user.id, db)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.id == task_id, ReviewTask.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review task not found",
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
    await verify_project_ownership(project_id, current_user.id, db)

    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.id == task_id, ReviewTask.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review task not found",
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
    """
    # Verify user has access to the project
    await verify_project_ownership(project_id, current_user.id, db)

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
            detail="Task not found",
        )

    # Extract Last-Event-ID header for reconnection support
    last_event_id = request.headers.get("Last-Event-ID")

    async def event_generator():
        async for event in sse_manager.connect(task_id, last_event_id):
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
