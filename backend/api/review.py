"""Review API routes."""

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from backend.api.deps import DBSession, CurrentUser
from backend.models import Project, ReviewTask, ReviewResult, AgentStep
from backend.schemas.review import (
    ReviewResponse,
    ReviewResultResponse,
    ReviewTaskResponse,
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


@router.post("", response_model=ReviewTaskResponse, status_code=status.HTTP_201_CREATED)
async def start_review(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ReviewTask:
    """Start a new review task for the project."""
    await verify_project_ownership(project_id, current_user.id, db)

    # Check if there's already a running task
    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.project_id == project_id)
        .where(ReviewTask.status.in_(["pending", "running"]))
    )
    existing_task = result.scalar_one_or_none()
    if existing_task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A review task is already running for this project",
        )

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
    run_review.delay(task.id)

    return task


@router.get("", response_model=ReviewResponse)
async def get_review_results(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ReviewResponse:
    """Get the latest review results for the project."""
    await verify_project_ownership(project_id, current_user.id, db)

    # Get latest completed task
    result = await db.execute(
        select(ReviewTask)
        .where(ReviewTask.project_id == project_id, ReviewTask.status == "completed")
        .order_by(ReviewTask.completed_at.desc())
        .limit(1)
    )
    task = result.scalar_one_or_none()

    if not task:
        # No completed review yet
        return ReviewResponse(
            summary={
                "total_requirements": 0,
                "compliant": 0,
                "non_compliant": 0,
                "critical": 0,
                "major": 0,
                "minor": 0,
            },
            findings=[],
        )

    # Get all results for this task
    result = await db.execute(
        select(ReviewResult)
        .where(ReviewResult.task_id == task.id)
        .order_by(
            ReviewResult.severity.asc(),  # critical first
            ReviewResult.created_at.asc(),
        )
    )
    findings = result.scalars().all()

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
    db: DBSession,
    current_user: CurrentUser,
):
    """Stream SSE events for a review task.

    This endpoint provides real-time updates about the review task progress,
    including agent steps, progress updates, and completion status.
    """
    # Verify user has access to the project
    await verify_project_ownership(project_id, current_user.id, db)

    async def event_generator():
        async for event in sse_manager.connect(task_id):
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
