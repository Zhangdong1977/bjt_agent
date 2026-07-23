"""Projects API routes."""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from backend.api.deps import DBSession, CurrentUser, is_interior_user
from backend.models import Project
from backend.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectListResponse
from backend.utils.time_utils import utc_now

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    db: DBSession,
    current_user: CurrentUser,
    project_type: str | None = Query(default=None, pattern="^(review|duplicate)$"),
) -> ProjectListResponse:
    """List non-deleted projects for the current user's history page."""
    query = select(Project).where(
        Project.user_id == current_user.id, Project.is_deleted.is_(False)
    )
    if project_type:
        query = query.where(Project.project_type == project_type)
    result = await db.execute(query.order_by(Project.created_at.desc()))
    projects = result.scalars().all()
    return ProjectListResponse(projects=projects)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    db: DBSession,
    current_user: CurrentUser,
) -> Project:
    """Create a new project."""
    project = Project(
        user_id=current_user.id,
        name=project_data.name,
        description=project_data.description,
        project_type=project_data.project_type,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> Project:
    """Get a project by ID.

    Internal users may view any project; regular users only their own.
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
    if not is_interior_user(current_user) and (
        project.user_id != current_user.id or project.is_deleted
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在或无权访问",
        )
    return project


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    project_data: ProjectUpdate,
    db: DBSession,
    current_user: CurrentUser,
) -> Project:
    """Update a project."""
    result = await db.execute(
        select(Project)
        .where(
            Project.id == project_id,
            Project.user_id == current_user.id,
            Project.is_deleted.is_(False),
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在或无权访问",
        )

    if project_data.name is not None:
        project.name = project_data.name
    if project_data.description is not None:
        project.description = project_data.description

    await db.flush()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> None:
    """Soft-delete a project.

    Regular users may hide their own projects from their history page. Internal
    users may soft-delete any project, while the documents and review results
    remain available to internal read paths.
    """
    query = select(Project).where(Project.id == project_id)
    if not is_interior_user(current_user):
        query = query.where(
            Project.user_id == current_user.id,
            Project.is_deleted.is_(False),
        )

    result = await db.execute(query)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在或无权访问",
        )

    if not project.is_deleted:
        project.is_deleted = True
        project.deleted_at = utc_now()
        project.deleted_by_user_id = current_user.id
        project.status = "deleted"

    await db.flush()
