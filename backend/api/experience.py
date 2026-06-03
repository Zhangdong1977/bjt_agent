"""Experience query API routes."""

import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import CurrentUser, DBSession
from backend.experience.models import ExperienceCase, ExperienceFeedback, ExperienceSkill, ExperienceClusterMembership
from backend.models import Project, User
from backend.schemas.feedback import PaginatedProjectSummary, ProjectFeedbackSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/experience", tags=["Experience"])


@router.get("/skills")
async def list_skills(
    db: DBSession,
    current_user: CurrentUser,
    group_id: str | None = None,
    skill_form: str | None = None,
    include_retired: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    query = select(ExperienceSkill)
    if group_id:
        query = query.where(ExperienceSkill.group_id == group_id)
    if skill_form:
        query = query.where(ExperienceSkill.skill_form == skill_form)
    if not include_retired:
        query = query.where(ExperienceSkill.retired_at.is_(None))
    query = query.order_by(ExperienceSkill.confidence.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    skills = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "cluster_id": s.cluster_id,
            "group_id": s.group_id,
            "name": s.name,
            "description": s.description,
            "content": s.content,
            "skill_form": s.skill_form,
            "confidence": s.confidence,
            "maturity_score": s.maturity_score,
            "maturity_detail": s.maturity_detail,
            "source_case_count": len(s.source_case_ids) if s.source_case_ids else 0,
            "retired_at": s.retired_at.isoformat() if s.retired_at else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in skills
    ]


@router.get("/cases")
async def list_cases(
    db: DBSession,
    current_user: CurrentUser,
    project_id: str | None = None,
    group_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    query = select(ExperienceCase)
    if project_id:
        query = query.where(ExperienceCase.project_id == project_id)
    if group_id:
        query = query.where(ExperienceCase.group_id == group_id)
    query = query.order_by(ExperienceCase.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    cases = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "task_id": c.task_id,
            "project_id": c.project_id,
            "rule_doc_name": c.rule_doc_name,
            "group_id": c.group_id,
            "user_id": c.user_id,
            "task_intent": c.task_intent,
            "approach": c.approach,
            "key_insight": c.key_insight,
            "quality_score": c.quality_score,
            "quality_score_llm": c.quality_score_llm,
            "quality_score_eval": c.quality_score_eval,
            "finding_count": c.finding_count,
            "raw_step_count": c.raw_step_count,
            "compressed_step_count": c.compressed_step_count,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in cases
    ]


@router.get("/clusters")
async def list_clusters(
    db: DBSession,
    current_user: CurrentUser,
    group_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    query = select(
        ExperienceClusterMembership.cluster_id,
        ExperienceClusterMembership.group_id,
        func.count(ExperienceClusterMembership.case_id).label("case_count"),
    ).group_by(
        ExperienceClusterMembership.cluster_id,
        ExperienceClusterMembership.group_id,
    )
    if group_id:
        query = query.where(ExperienceClusterMembership.group_id == group_id)
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    clusters = []
    for row in result.all():
        clusters.append({
            "cluster_id": row.cluster_id,
            "group_id": row.group_id,
            "case_count": row.case_count,
        })
    return clusters


@router.get("/quality-trend")
async def quality_trend(
    db: DBSession,
    current_user: CurrentUser,
    group_id: str | None = None,
    days: int = 30,
) -> dict:
    since = datetime.utcnow() - __import__("datetime").timedelta(days=days)

    query = select(
        func.date_trunc("day", ExperienceCase.created_at).label("date"),
        func.avg(ExperienceCase.quality_score).label("avg_quality"),
        func.count(ExperienceCase.id).label("case_count"),
    ).where(
        ExperienceCase.created_at >= since,
    )
    if group_id:
        query = query.where(ExperienceCase.group_id == group_id)
    query = query.group_by("date").order_by("date")

    result = await db.execute(query)
    trend = [
        {
            "date": str(row.date),
            "avg_quality": round(float(row.avg_quality), 3) if row.avg_quality else 0,
            "case_count": row.case_count,
        }
        for row in result.all()
    ]

    skill_count_result = await db.execute(
        select(func.count(ExperienceSkill.id)).where(
            ExperienceSkill.retired_at.is_(None),
        )
    )
    active_skills = skill_count_result.scalar() or 0

    avg_confidence_result = await db.execute(
        select(func.avg(ExperienceSkill.confidence)).where(
            ExperienceSkill.retired_at.is_(None),
        )
    )
    avg_confidence = float(avg_confidence_result.scalar() or 0)

    return {
        "trend": trend,
        "active_skills": active_skills,
        "avg_confidence": round(avg_confidence, 3),
    }


@router.get("/projects-summary", response_model=PaginatedProjectSummary)
async def projects_feedback_summary(
    db: DBSession,
    current_user: CurrentUser,
    limit: int = 20,
    offset: int = 0,
    time_range: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    username: str | None = None,
    project_name: str | None = None,
    project_id: str | None = None,
) -> PaginatedProjectSummary:
    """List all projects with aggregated feedback counts, paginated with filters."""
    # Build base query with JOINs and aggregation
    base_columns = (
        Project.id.label("project_id"),
        Project.name.label("project_name"),
        Project.user_id,
        User.username,
        func.count(ExperienceFeedback.id).label("total_feedback"),
        func.count(
            case(
                (ExperienceFeedback.status.in_(["accepted", "rejected"]), ExperienceFeedback.id),
            )
        ).label("reviewed_feedback"),
        func.count(
            case(
                (ExperienceFeedback.status == "pending", ExperienceFeedback.id),
            )
        ).label("unreviewed_feedback"),
        Project.created_at,
    )

    query = (
        select(*base_columns)
        .join(User, Project.user_id == User.id)
        .outerjoin(
            ExperienceFeedback,
            and_(
                ExperienceFeedback.project_id == Project.id,
                ExperienceFeedback.status != "superseded",
            ),
        )
        .group_by(Project.id, User.username)
    )

    # Apply time range filter
    if time_range == "today":
        today_start = datetime.combine(date.today(), datetime.min.time())
        query = query.where(Project.created_at >= today_start)
    elif time_range == "7d":
        since = datetime.utcnow() - timedelta(days=7)
        query = query.where(Project.created_at >= since)
    elif time_range == "30d":
        since = datetime.utcnow() - timedelta(days=30)
        query = query.where(Project.created_at >= since)
    elif time_range == "custom" and start_date and end_date:
        sd = datetime.combine(date.fromisoformat(start_date), datetime.min.time())
        ed = datetime.combine(date.fromisoformat(end_date), datetime.max.time())
        query = query.where(Project.created_at.between(sd, ed))

    # Apply keyword filters
    if username:
        query = query.where(User.username.ilike(f"%{username}%"))
    if project_name:
        query = query.where(Project.name.ilike(f"%{project_name}%"))
    if project_id:
        query = query.where(Project.id.ilike(f"%{project_id}%"))

    # Count total (before pagination)
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Apply ordering and pagination
    query = query.order_by(Project.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    rows = result.all()

    items = [
        ProjectFeedbackSummary(
            project_id=row.project_id,
            project_name=row.project_name,
            user_id=row.user_id,
            username=row.username,
            total_feedback=row.total_feedback,
            reviewed_feedback=row.reviewed_feedback,
            unreviewed_feedback=row.unreviewed_feedback,
            created_at=row.created_at,
        )
        for row in rows
    ]

    return PaginatedProjectSummary(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )
