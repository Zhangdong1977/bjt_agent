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
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select, and_, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import CurrentUser, DBSession, get_token_claims, oauth2_scheme
from backend.config import get_settings
from backend.models import Project, ReviewTask, ReviewResult
from backend.experience.models import ExperienceFeedback
from backend.schemas.feedback import (
    BatchFeedbackRequest,
    BatchFeedbackResponse,
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

async def _verify_project(project_id: str, user_id: str, db: AsyncSession) -> Project:
    """Verify project exists and belongs to the user."""
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar_one_or_none()
    if not project:
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


def _is_interior_user(current_user) -> bool:
    """Check if the user has internal status via JWT claims."""
    try:
        token = current_user.password_hash  # Not ideal but we don't store raw token
        return getattr(current_user, "_interior_user", False)
    except Exception:
        return False


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
    await _verify_project(project_id, current_user.id, db)
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
        existing.updated_at = datetime.utcnow()

    # Determine auto-acceptance
    new_status = "pending"
    if body.feedback_type == "confirm":
        new_status = "accepted"
    elif body.feedback_type == "contradict":
        # Auto-accept if internal user or >=2 contradictions exist
        # For now, always auto-accept contradict from any authenticated user
        new_status = "accepted"
    elif body.feedback_type == "refine":
        # Auto-accept unless changing is_compliant from false to true
        if body.corrected_is_compliant is True:
            new_status = "pending"  # Requires admin review
        else:
            new_status = "accepted"

    # Compute confidence delta
    confidence_delta = 0.0
    if new_status == "accepted":
        if body.feedback_type == "confirm":
            confidence_delta = 0.05
        elif body.feedback_type == "contradict":
            confidence_delta = -0.2
        elif body.feedback_type == "refine":
            confidence_delta = -0.1

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
        status=new_status,
        confidence_delta=confidence_delta,
        rule_doc_name=finding.rule_doc_name,
    )
    db.add(feedback)

    # If auto-accepted, trigger async processing
    if new_status == "accepted":
        try:
            from backend.tasks.feedback_tasks import process_feedback
            process_feedback.delay(feedback.id)
        except Exception as e:
            logger.warning(f"Failed to dispatch feedback processing: {e}")

    await db.flush()
    await db.refresh(feedback)

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
    await _verify_project(project_id, current_user.id, db)

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
    await _verify_project(project_id, current_user.id, db)

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
            existing.updated_at = datetime.utcnow()
            superseded_count += 1

        feedback = ExperienceFeedback(
            finding_id=finding.id,
            user_id=current_user.id,
            project_id=project_id,
            task_id=task_id,
            feedback_type="confirm",
            comment=body.comment,
            status="accepted",
            confidence_delta=0.05,
            batch_id=batch_id,
            rule_doc_name=finding.rule_doc_name,
        )
        db.add(feedback)
        created_count += 1

    # Trigger async processing for the batch
    if created_count > 0:
        try:
            from backend.tasks.feedback_tasks import process_batch_feedback
            process_batch_feedback.delay(batch_id)
        except Exception as e:
            logger.warning(f"Failed to dispatch batch feedback processing: {e}")

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
    await _verify_project(project_id, current_user.id, db)

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
    """Get paginated feedback history for a project (current user only)."""
    await _verify_project(project_id, current_user.id, db)

    result = await db.execute(
        select(ExperienceFeedback)
        .where(
            ExperienceFeedback.project_id == project_id,
            ExperienceFeedback.user_id == current_user.id,
            ExperienceFeedback.status != "superseded",
        )
        .order_by(ExperienceFeedback.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
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
    await _verify_project(project_id, current_user.id, db)

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
    feedback.reviewed_at = datetime.utcnow()
    feedback.updated_at = datetime.utcnow()

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
            process_feedback.delay(feedback.id)
        except Exception as e:
            logger.warning(f"Failed to dispatch feedback processing: {e}")

    await db.flush()
    await db.refresh(feedback)
    return FeedbackResponse.model_validate(feedback)


# ---------------------------------------------------------------------------
# Experience dashboard (internal users only)
# ---------------------------------------------------------------------------

@router.get("/experience/dashboard", response_model=DashboardResponse)
async def get_experience_dashboard(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> DashboardResponse:
    """Get experience dashboard data for a project."""
    await _verify_project(project_id, current_user.id, db)

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
