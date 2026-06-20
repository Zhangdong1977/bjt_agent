"""Feedback API routes for experience self-learning.

Provides endpoints for:
- Submitting per-finding feedback (confirm / contradict / refine)
- Batch-confirming all findings for a sub-agent
- Admin review of pending feedback
- Feedback summary and history
- Experience dashboard (internal users only)
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select, and_, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import CurrentUser, DBSession, InteriorUser, get_token_claims, oauth2_scheme, is_interior_user
from backend.config import get_settings
from backend.models import Project, ReviewTask, ReviewResult, User
from backend.experience.models import ExperienceFeedback
from backend.schemas.feedback import (
    BatchFeedbackRequest,
    BatchFeedbackResponse,
    BatchFeedbackReviewRequest,
    BatchFeedbackReviewResponse,
    FeedbackCreateRequest,
    FeedbackResponse,
    FeedbackReviewRequest,
    FeedbackSummaryResponse,
    DashboardResponse,
    ConfidenceTimelinePoint,
    SkillSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["Feedback"])
settings = get_settings()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _verify_project(
    project_id: str, current_user: User, db: AsyncSession, *, allow_interior: bool = False,
) -> Project:
    """Verify project exists and the caller may access it.

    Regular users may only access their own projects. When ``allow_interior``
    is set, internal users (see :func:`is_interior_user`) may access any
    project — used by review / dashboard endpoints surfaced on the experience
    dashboard. User-side submission endpoints keep ``allow_interior=False``.
    """
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if allow_interior and is_interior_user(current_user):
        return project
    if project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _verify_finding(
    finding_id: str, project_id: str, db: AsyncSession,
) -> ReviewResult:
    """Verify finding exists and belongs to a task in the given project."""
    result = await db.execute(
        select(ReviewResult)
        .join(ReviewTask, ReviewResult.task_id == ReviewTask.id)
        .where(
            ReviewResult.id == finding_id,
            ReviewTask.project_id == project_id,
        )
    )
    finding = result.scalar_one_or_none()
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    return finding


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/findings/{finding_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_feedback(
    project_id: str,
    finding_id: str,
    body: FeedbackCreateRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> FeedbackResponse:
    """Submit feedback for a single finding."""
    await _verify_project(project_id, current_user, db)
    finding = await _verify_finding(finding_id, project_id, db)

    # Only allow feedback on non-compliant findings
    if finding.is_compliant:
        raise HTTPException(
            status_code=400,
            detail="合规项不支持反馈",
        )

    # Supersede any existing active feedback from this user on this finding
    existing_result = await db.execute(
        select(ExperienceFeedback).where(
            ExperienceFeedback.finding_id == finding_id,
            ExperienceFeedback.user_id == current_user.id,
            ExperienceFeedback.status != "superseded",
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        existing.status = "superseded"
        existing.updated_at = datetime.now(timezone.utc)

    # All feedback requires admin review before taking effect.
    # confidence_delta is computed at review time (see review_feedback endpoint).
    feedback = ExperienceFeedback(
        finding_id=finding_id,
        user_id=current_user.id,
        project_id=project_id,
        task_id=finding.task_id,
        feedback_type=body.feedback_type,
        contradict_reason=body.contradict_reason,
        corrected_severity=body.corrected_severity,
        corrected_suggestion=body.corrected_suggestion,
        corrected_is_compliant=body.corrected_is_compliant,
        comment=body.comment,
        status="pending",
        confidence_delta=0.0,
        rule_doc_name=finding.rule_doc_name,
    )
    db.add(feedback)
    await db.flush()
    await db.refresh(feedback)

    # No downstream processing — admin must review first.

    return FeedbackResponse.model_validate(feedback)


@router.get(
    "/findings/{finding_id}/feedback",
    response_model=list[FeedbackResponse],
)
async def get_finding_feedback(
    project_id: str,
    finding_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> list[FeedbackResponse]:
    """Get feedback history for a specific finding."""
    await _verify_project(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(ExperienceFeedback)
        .where(
            ExperienceFeedback.finding_id == finding_id,
            ExperienceFeedback.status != "superseded",
        )
        .order_by(ExperienceFeedback.created_at.desc())
    )
    feedbacks = result.scalars().all()
    return [FeedbackResponse.model_validate(f) for f in feedbacks]


@router.post(
    "/tasks/{task_id}/batch-feedback",
    response_model=BatchFeedbackResponse,
)
async def batch_confirm_findings(
    project_id: str,
    task_id: str,
    body: BatchFeedbackRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> BatchFeedbackResponse:
    """Batch-confirm all non-compliant findings for a task or sub-agent."""
    await _verify_project(project_id, current_user, db)

    # Verify task belongs to project
    task_result = await db.execute(
        select(ReviewTask).where(
            ReviewTask.id == task_id,
            ReviewTask.project_id == project_id,
        )
    )
    task = task_result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Get all non-compliant findings, optionally filtered by rule_doc_name
    query = select(ReviewResult).where(
        ReviewResult.task_id == task_id,
        ReviewResult.is_compliant == False,  # noqa: E712
    )
    if body.rule_doc_name:
        query = query.where(ReviewResult.rule_doc_name == body.rule_doc_name)

    findings_result = await db.execute(query)
    findings = findings_result.scalars().all()

    batch_id = str(uuid.uuid4())
    created_count = 0
    superseded_count = 0

    for finding in findings:
        # Check for existing active feedback
        existing_result = await db.execute(
            select(ExperienceFeedback).where(
                ExperienceFeedback.finding_id == finding.id,
                ExperienceFeedback.user_id == current_user.id,
                ExperienceFeedback.status != "superseded",
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            existing.status = "superseded"
            existing.updated_at = datetime.now(timezone.utc)
            superseded_count += 1

        # All feedback requires admin review; confidence computed at review time.
        feedback = ExperienceFeedback(
            finding_id=finding.id,
            user_id=current_user.id,
            project_id=project_id,
            task_id=task_id,
            feedback_type="confirm",
            comment=body.comment,
            status="pending",
            confidence_delta=0.0,
            batch_id=batch_id,
            rule_doc_name=finding.rule_doc_name,
        )
        db.add(feedback)
        created_count += 1

    # No downstream processing — admin must review first.

    await db.flush()
    return BatchFeedbackResponse(
        created_count=created_count,
        superseded_count=superseded_count,
    )


@router.get("/feedback/summary", response_model=FeedbackSummaryResponse)
async def get_feedback_summary(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> FeedbackSummaryResponse:
    """Get aggregated feedback statistics for a project."""
    await _verify_project(project_id, current_user, db, allow_interior=True)

    # Total feedback count
    total_result = await db.execute(
        select(func.count(ExperienceFeedback.id)).where(
            ExperienceFeedback.project_id == project_id,
            ExperienceFeedback.status == "accepted",
        )
    )
    total_feedback = total_result.scalar() or 0

    # By type
    type_result = await db.execute(
        select(
            ExperienceFeedback.feedback_type,
            func.count(ExperienceFeedback.id),
        )
        .where(
            ExperienceFeedback.project_id == project_id,
            ExperienceFeedback.status == "accepted",
        )
        .group_by(ExperienceFeedback.feedback_type)
    )
    by_type = {row[0]: row[1] for row in type_result.all()}

    # By status
    status_result = await db.execute(
        select(
            ExperienceFeedback.status,
            func.count(ExperienceFeedback.id),
        )
        .where(ExperienceFeedback.project_id == project_id)
        .group_by(ExperienceFeedback.status)
    )
    by_status = {row[0]: row[1] for row in status_result.all()}

    # Agreement rate
    confirm_count = by_type.get("confirm", 0)
    contradict_count = by_type.get("contradict", 0)
    total_with_type = confirm_count + contradict_count
    agreement_rate = (
        round(confirm_count / total_with_type, 3) if total_with_type > 0 else 0.0
    )

    # Top contradicted rules
    rules_result = await db.execute(
        select(
            ExperienceFeedback.rule_doc_name,
            func.count(ExperienceFeedback.id),
        )
        .where(
            ExperienceFeedback.project_id == project_id,
            ExperienceFeedback.feedback_type == "contradict",
            ExperienceFeedback.status == "accepted",
            ExperienceFeedback.rule_doc_name.isnot(None),
        )
        .group_by(ExperienceFeedback.rule_doc_name)
        .order_by(func.count(ExperienceFeedback.id).desc())
        .limit(5)
    )
    top_contradicted_rules = [
        {"rule_doc_name": row[0], "count": row[1]}
        for row in rules_result.all()
    ]

    return FeedbackSummaryResponse(
        total_feedback=total_feedback,
        by_type=by_type,
        by_status=by_status,
        agreement_rate=agreement_rate,
        top_contradicted_rules=top_contradicted_rules,
    )


@router.get("/feedback/history", response_model=list[FeedbackResponse])
async def get_feedback_history(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
    limit: int = 50,
    offset: int = 0,
) -> list[FeedbackResponse]:
    """Get paginated feedback history for a project.

    Interior users see feedback from all users on the project (used by the
    experience dashboard when reviewing another user's project); regular users
    only see their own feedback.
    """
    await _verify_project(project_id, current_user, db, allow_interior=True)

    query = (
        select(ExperienceFeedback)
        .where(
            ExperienceFeedback.project_id == project_id,
            ExperienceFeedback.status != "superseded",
        )
    )
    if not is_interior_user(current_user):
        query = query.where(ExperienceFeedback.user_id == current_user.id)
    query = query.order_by(ExperienceFeedback.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    feedbacks = result.scalars().all()
    return [FeedbackResponse.model_validate(f) for f in feedbacks]


@router.get("/feedback/pending", response_model=list[FeedbackResponse])
async def get_pending_feedback(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
    limit: int = 100,
    offset: int = 0,
) -> list[FeedbackResponse]:
    """Get pending feedback for a project (interior users only).

    Returns feedback records with status='pending' that need admin review.
    Interior users can review and accept/reject these feedbacks.
    """
    await _verify_project(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(ExperienceFeedback)
        .where(
            ExperienceFeedback.project_id == project_id,
            ExperienceFeedback.status == "pending",
        )
        .order_by(ExperienceFeedback.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    feedbacks = result.scalars().all()
    return [FeedbackResponse.model_validate(f) for f in feedbacks]


@router.get("/feedback/all", response_model=list[FeedbackResponse])
async def get_all_feedback(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
    status: str | None = None,
    feedback_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[FeedbackResponse]:
    """Get all feedback for a project (interior users only).

    Supports filtering by status and feedback_type.
    """
    await _verify_project(project_id, current_user, db, allow_interior=True)

    query = select(ExperienceFeedback).where(
        ExperienceFeedback.project_id == project_id,
        ExperienceFeedback.status != "superseded",
    )
    if status:
        query = query.where(ExperienceFeedback.status == status)
    if feedback_type:
        query = query.where(ExperienceFeedback.feedback_type == feedback_type)
    query = query.order_by(ExperienceFeedback.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    feedbacks = result.scalars().all()
    return [FeedbackResponse.model_validate(f) for f in feedbacks]


# ---------------------------------------------------------------------------
# Admin endpoints (internal users only)
# ---------------------------------------------------------------------------

@router.patch(
    "/feedback/{feedback_id}/review",
    response_model=FeedbackResponse,
)
async def review_feedback(
    project_id: str,
    feedback_id: str,
    body: FeedbackReviewRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> FeedbackResponse:
    """Admin endpoint to accept or reject pending feedback."""
    await _verify_project(project_id, current_user, db, allow_interior=True)

    result = await db.execute(
        select(ExperienceFeedback).where(
            ExperienceFeedback.id == feedback_id,
            ExperienceFeedback.project_id == project_id,
            ExperienceFeedback.status == "pending",
        )
    )
    feedback = result.scalar_one_or_none()
    if not feedback:
        raise HTTPException(status_code=404, detail="Pending feedback not found")

    feedback.status = "accepted" if body.action == "accept" else "rejected"
    feedback.reviewed_by = current_user.id
    feedback.reviewed_at = datetime.now(timezone.utc)
    feedback.updated_at = datetime.now(timezone.utc)

    # Compute confidence delta on acceptance
    if feedback.status == "accepted":
        if feedback.feedback_type == "confirm":
            feedback.confidence_delta = 0.05
        elif feedback.feedback_type == "contradict":
            feedback.confidence_delta = -0.2
        elif feedback.feedback_type == "refine":
            feedback.confidence_delta = -0.1

        try:
            from backend.tasks.feedback_tasks import process_feedback
            process_feedback.delay(str(feedback.id))
        except Exception as e:
            logger.warning(f"Failed to dispatch feedback processing: {e}")

    await db.flush()
    await db.refresh(feedback)
    return FeedbackResponse.model_validate(feedback)


@router.post(
    "/feedback/batch-review",
    response_model=BatchFeedbackReviewResponse,
)
async def batch_review_feedback(
    project_id: str,
    body: BatchFeedbackReviewRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> BatchFeedbackReviewResponse:
    """Admin endpoint to batch-accept or batch-reject all pending feedback.

    Optionally filter by task_id or batch_id to narrow the scope.
    """
    await _verify_project(project_id, current_user, db, allow_interior=True)

    # Build query for pending feedback
    query = select(ExperienceFeedback).where(
        ExperienceFeedback.project_id == project_id,
        ExperienceFeedback.status == "pending",
    )
    if body.task_id:
        query = query.where(ExperienceFeedback.task_id == body.task_id)
    if body.batch_id:
        query = query.where(ExperienceFeedback.batch_id == body.batch_id)

    result = await db.execute(query)
    pending_feedbacks = result.scalars().all()

    if not pending_feedbacks:
        return BatchFeedbackReviewResponse(reviewed_count=0, action=body.action)

    new_status = "accepted" if body.action == "accept" else "rejected"
    reviewed_count = 0

    for feedback in pending_feedbacks:
        feedback.status = new_status
        feedback.reviewed_by = current_user.id
        feedback.reviewed_at = datetime.now(timezone.utc)
        feedback.updated_at = datetime.now(timezone.utc)

        if new_status == "accepted":
            # Compute confidence delta based on feedback type
            if feedback.feedback_type == "confirm":
                feedback.confidence_delta = 0.05
            elif feedback.feedback_type == "contradict":
                feedback.confidence_delta = -0.2
            elif feedback.feedback_type == "refine":
                feedback.confidence_delta = -0.1

            # Trigger individual processing
            try:
                from backend.tasks.feedback_tasks import process_feedback
                process_feedback.delay(str(feedback.id))
            except Exception as e:
                logger.warning(
                    f"Failed to dispatch feedback {feedback.id} processing: {e}"
                )

        reviewed_count += 1

    await db.flush()
    return BatchFeedbackReviewResponse(
        reviewed_count=reviewed_count,
        action=body.action,
    )


# ---------------------------------------------------------------------------
# Experience dashboard (internal users only)
# ---------------------------------------------------------------------------

@router.get("/experience/dashboard", response_model=DashboardResponse)
async def get_experience_dashboard(
    project_id: str,
    db: DBSession,
    current_user: InteriorUser,
) -> DashboardResponse:
    """Get experience dashboard data for a project."""
    await _verify_project(project_id, current_user, db, allow_interior=True)

    # Feedback stats
    total_result = await db.execute(
        select(func.count(ExperienceFeedback.id)).where(
            ExperienceFeedback.project_id == project_id,
            ExperienceFeedback.status == "accepted",
        )
    )
    total_feedback = total_result.scalar() or 0

    type_result = await db.execute(
        select(
            ExperienceFeedback.feedback_type,
            func.count(ExperienceFeedback.id),
        )
        .where(
            ExperienceFeedback.project_id == project_id,
            ExperienceFeedback.status == "accepted",
        )
        .group_by(ExperienceFeedback.feedback_type)
    )
    feedback_by_type = {row[0]: row[1] for row in type_result.all()}

    confirm_count = feedback_by_type.get("confirm", 0)
    contradict_count = feedback_by_type.get("contradict", 0)
    total_with_type = confirm_count + contradict_count
    agreement_rate = (
        round(confirm_count / total_with_type, 3) if total_with_type > 0 else 0.0
    )

    # Top contradicted rules
    rules_result = await db.execute(
        select(
            ExperienceFeedback.rule_doc_name,
            func.count(ExperienceFeedback.id),
        )
        .where(
            ExperienceFeedback.project_id == project_id,
            ExperienceFeedback.feedback_type == "contradict",
            ExperienceFeedback.status == "accepted",
            ExperienceFeedback.rule_doc_name.isnot(None),
        )
        .group_by(ExperienceFeedback.rule_doc_name)
        .order_by(func.count(ExperienceFeedback.id).desc())
        .limit(5)
    )
    top_contradicted_rules = [
        {"rule_doc_name": row[0], "count": row[1]}
        for row in rules_result.all()
    ]

    # Recent feedback
    recent_result = await db.execute(
        select(ExperienceFeedback)
        .where(
            ExperienceFeedback.project_id == project_id,
            ExperienceFeedback.status != "superseded",
        )
        .order_by(ExperienceFeedback.created_at.desc())
        .limit(20)
    )
    recent_feedback = [
        FeedbackResponse.model_validate(f) for f in recent_result.scalars().all()
    ]

    return DashboardResponse(
        active_skills=0,  # Will be populated when experience_skills table exists
        retired_skills=0,
        total_feedback=total_feedback,
        agreement_rate=agreement_rate,
        avg_confidence=0.0,
        avg_maturity=0.0,
        confidence_timeline=[],
        feedback_by_type=feedback_by_type,
        top_contradicted_rules=top_contradicted_rules,
        recent_feedback=recent_feedback,
        skills=[],
    )
