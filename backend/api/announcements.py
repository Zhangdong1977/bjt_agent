"""System announcement API routes.

三类端点：
- **公开**：``GET /announcements/public`` —— 登录页跑马灯用，无需登录，仅返回公开展示字段。
- **用户**（已登录）：列表/未读/详情/标记已读/一键已读，每条带当前用户的已读状态。
- **管理**（内部用户 ``InteriorUser``）：发布/管理列表/编辑/删除。

可见性 = ``is_active=True`` 且 ``published_at <= now`` 且（``expires_at`` 为空或未过期）。

路由顺序注意：所有字面量路径（``/public``、``/manage``、``/unread-count``、空路径）
必须先于参数路径 ``/{announcement_id}`` 注册，否则 FastAPI 会把 ``/manage`` 当成
``announcement_id="manage"`` 匹配掉。
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import CurrentUser, DBSession, InteriorUser
from backend.models import SystemAnnouncement, SystemAnnouncementRead, User
from backend.schemas.announcement import (
    AnnouncementCreateRequest,
    AnnouncementListResponse,
    AnnouncementManageResponse,
    AnnouncementResponse,
    AnnouncementUpdateRequest,
    MarkAllReadResponse,
    PublicAnnouncementResponse,
    UnreadCountResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/announcements", tags=["Announcements"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _visible_filter(now: datetime):
    """active + 已发布 + 未过期的过滤条件。"""
    return and_(
        SystemAnnouncement.is_active.is_(True),
        SystemAnnouncement.published_at <= now,
        or_(
            SystemAnnouncement.expires_at.is_(None),
            SystemAnnouncement.expires_at > now,
        ),
    )


def _read_exists_subquery(user_id: str):
    """「该用户已读」的 EXISTS 子查询，用于过滤未读。"""
    return (
        select(SystemAnnouncementRead.id)
        .where(
            SystemAnnouncementRead.user_id == user_id,
            SystemAnnouncementRead.announcement_id == SystemAnnouncement.id,
        )
        .exists()
    )


async def _author_name_map(db: AsyncSession, author_ids: set[str]) -> dict[str, str]:
    """批量取发布人展示名（nickname 优先，回退 username）。"""
    if not author_ids:
        return {}
    result = await db.execute(
        select(User.id, User.username, User.nickname).where(User.id.in_(author_ids))
    )
    return {row[0]: (row[2] or row[1]) for row in result.all()}


async def _user_read_map(
    db: AsyncSession, user_id: str, announcement_ids: list[str]
) -> dict[str, datetime]:
    """取当前用户对给定公告的已读时间映射。"""
    if not announcement_ids:
        return {}
    result = await db.execute(
        select(
            SystemAnnouncementRead.announcement_id,
            SystemAnnouncementRead.read_at,
        ).where(
            SystemAnnouncementRead.user_id == user_id,
            SystemAnnouncementRead.announcement_id.in_(announcement_ids),
        )
    )
    return {row[0]: row[1] for row in result.all()}


async def _total_users(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(User.id)))
    return result.scalar() or 0


def _self_name(user: User) -> str:
    return user.nickname or user.username


def _to_user_response(
    ann: SystemAnnouncement,
    *,
    read_map: dict[str, datetime],
    author_map: dict[str, str],
) -> AnnouncementResponse:
    read_at = read_map.get(ann.id)
    return AnnouncementResponse(
        id=ann.id,
        title=ann.title,
        content=ann.content,
        severity=ann.severity,
        is_active=ann.is_active,
        published_at=ann.published_at,
        expires_at=ann.expires_at,
        created_by=ann.created_by,
        created_by_name=author_map.get(ann.created_by) if ann.created_by else None,
        created_at=ann.created_at,
        updated_at=ann.updated_at,
        is_read=read_at is not None,
        read_at=read_at,
    )


def _to_manage_response(
    ann: SystemAnnouncement,
    *,
    read_count: int,
    total_users: int,
    author_name: str | None,
) -> AnnouncementManageResponse:
    return AnnouncementManageResponse(
        id=ann.id,
        title=ann.title,
        content=ann.content,
        severity=ann.severity,
        is_active=ann.is_active,
        published_at=ann.published_at,
        expires_at=ann.expires_at,
        created_by=ann.created_by,
        created_by_name=author_name,
        created_at=ann.created_at,
        updated_at=ann.updated_at,
        read_count=read_count,
        total_users=total_users,
    )


# ---------------------------------------------------------------------------
# 字面量路径（必须先于 /{announcement_id} 注册）
# ---------------------------------------------------------------------------


@router.get("/public", response_model=list[PublicAnnouncementResponse])
async def list_public_announcements(
    db: DBSession,
    limit: int = Query(20, ge=1, le=100),
) -> list[PublicAnnouncementResponse]:
    """登录页跑马灯：返回当前可见的公告（仅公开展示字段）。无需登录。"""
    now = _utc_now()
    result = await db.execute(
        select(SystemAnnouncement)
        .where(_visible_filter(now))
        .order_by(SystemAnnouncement.published_at.desc())
        .limit(limit)
    )
    items = result.scalars().all()
    return [PublicAnnouncementResponse.model_validate(a) for a in items]


@router.get("/manage", response_model=list[AnnouncementManageResponse])
async def list_announcements_for_manage(
    db: DBSession,
    current_user: InteriorUser,
    include_inactive: bool = True,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[AnnouncementManageResponse]:
    """管理端列表（内部用户，含下线/过期），带已读统计。"""
    query = select(SystemAnnouncement)
    if not include_inactive:
        query = query.where(SystemAnnouncement.is_active.is_(True))
    query = (
        query.order_by(SystemAnnouncement.published_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    items = result.scalars().all()
    if not items:
        return []

    read_counts_result = await db.execute(
        select(
            SystemAnnouncementRead.announcement_id,
            func.count(SystemAnnouncementRead.id),
        )
        .where(SystemAnnouncementRead.announcement_id.in_([a.id for a in items]))
        .group_by(SystemAnnouncementRead.announcement_id)
    )
    read_counts = {row[0]: row[1] for row in read_counts_result.all()}

    total_users = await _total_users(db)
    author_map = await _author_name_map(
        db, {a.created_by for a in items if a.created_by}
    )

    return [
        _to_manage_response(
            a,
            read_count=read_counts.get(a.id, 0),
            total_users=total_users,
            author_name=author_map.get(a.created_by) if a.created_by else None,
        )
        for a in items
    ]


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    db: DBSession,
    current_user: CurrentUser,
) -> UnreadCountResponse:
    """当前用户未读公告数（顶栏角标 + 弹窗触发用）。"""
    now = _utc_now()
    result = await db.execute(
        select(func.count(SystemAnnouncement.id)).where(
            _visible_filter(now),
            ~_read_exists_subquery(current_user.id),
        )
    )
    return UnreadCountResponse(unread_count=result.scalar() or 0)


@router.get("", response_model=AnnouncementListResponse)
async def list_announcements(
    db: DBSession,
    current_user: CurrentUser,
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AnnouncementListResponse:
    """当前用户可见的公告列表（带已读状态）+ 未读计数。"""
    now = _utc_now()
    read_exists = _read_exists_subquery(current_user.id)

    # 未读总数（始终为全量未读，不受 unread_only 影响）
    unread_result = await db.execute(
        select(func.count(SystemAnnouncement.id)).where(
            _visible_filter(now), ~read_exists
        )
    )
    unread_count = unread_result.scalar() or 0

    query = select(SystemAnnouncement).where(_visible_filter(now))
    if unread_only:
        query = query.where(~read_exists)
    query = (
        query.order_by(SystemAnnouncement.published_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    items = result.scalars().all()

    read_map = await _user_read_map(db, current_user.id, [a.id for a in items])
    author_map = await _author_name_map(
        db, {a.created_by for a in items if a.created_by}
    )
    response_items = [
        _to_user_response(a, read_map=read_map, author_map=author_map)
        for a in items
    ]

    total_query = select(func.count(SystemAnnouncement.id)).where(
        _visible_filter(now)
    )
    if unread_only:
        total_query = total_query.where(~read_exists)
    total_result = await db.execute(total_query)
    total = total_result.scalar() or 0

    return AnnouncementListResponse(
        items=response_items, total=total, unread_count=unread_count
    )


@router.post("", response_model=AnnouncementManageResponse, status_code=status.HTTP_201_CREATED)
async def create_announcement(
    body: AnnouncementCreateRequest,
    db: DBSession,
    current_user: InteriorUser,
) -> AnnouncementManageResponse:
    """发布系统公告（内部用户）。"""
    now = _utc_now()
    ann = SystemAnnouncement(
        title=body.title,
        content=body.content,
        severity=body.severity,
        is_active=body.is_active,
        published_at=body.published_at or now,
        expires_at=body.expires_at,
        created_by=current_user.id,
    )
    db.add(ann)
    await db.flush()
    await db.refresh(ann)
    return _to_manage_response(
        ann,
        read_count=0,
        total_users=await _total_users(db),
        author_name=_self_name(current_user),
    )


@router.post("/mark-all-read", response_model=MarkAllReadResponse)
async def mark_all_as_read(
    db: DBSession,
    current_user: CurrentUser,
) -> MarkAllReadResponse:
    """一键把当前所有可见未读公告标记为已读。"""
    now = _utc_now()
    visible_result = await db.execute(
        select(SystemAnnouncement.id).where(_visible_filter(now))
    )
    visible_ids = [row[0] for row in visible_result.all()]
    if not visible_ids:
        return MarkAllReadResponse(marked_count=0)

    already_result = await db.execute(
        select(SystemAnnouncementRead.announcement_id).where(
            SystemAnnouncementRead.user_id == current_user.id,
            SystemAnnouncementRead.announcement_id.in_(visible_ids),
        )
    )
    already = {row[0] for row in already_result.all()}
    to_mark = [aid for aid in visible_ids if aid not in already]
    for aid in to_mark:
        db.add(
            SystemAnnouncementRead(
                announcement_id=aid, user_id=current_user.id, read_at=now
            )
        )
    await db.flush()
    return MarkAllReadResponse(marked_count=len(to_mark))


# ---------------------------------------------------------------------------
# 参数路径 /{announcement_id}（必须放在所有字面量路径之后）
# ---------------------------------------------------------------------------


@router.get("/{announcement_id}", response_model=AnnouncementResponse)
async def get_announcement(
    announcement_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> AnnouncementResponse:
    """单条公告详情（带当前用户已读状态）。仅返回可见公告。"""
    now = _utc_now()
    result = await db.execute(
        select(SystemAnnouncement).where(
            SystemAnnouncement.id == announcement_id,
            _visible_filter(now),
        )
    )
    ann = result.scalar_one_or_none()
    if not ann:
        raise HTTPException(status_code=404, detail="公告不存在或已下线")

    read_map = await _user_read_map(db, current_user.id, [ann.id])
    author_map = await _author_name_map(
        db, {ann.created_by} if ann.created_by else set()
    )
    return _to_user_response(ann, read_map=read_map, author_map=author_map)


@router.post("/{announcement_id}/read", response_model=AnnouncementResponse)
async def mark_as_read(
    announcement_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> AnnouncementResponse:
    """标记一条公告为已读（幂等：重复标记只更新 read_at）。"""
    now = _utc_now()
    result = await db.execute(
        select(SystemAnnouncement).where(
            SystemAnnouncement.id == announcement_id,
            _visible_filter(now),
        )
    )
    ann = result.scalar_one_or_none()
    if not ann:
        raise HTTPException(status_code=404, detail="公告不存在或已下线")

    existing = await db.execute(
        select(SystemAnnouncementRead).where(
            SystemAnnouncementRead.announcement_id == announcement_id,
            SystemAnnouncementRead.user_id == current_user.id,
        )
    )
    read_row = existing.scalar_one_or_none()
    if read_row is None:
        db.add(
            SystemAnnouncementRead(
                announcement_id=announcement_id,
                user_id=current_user.id,
                read_at=now,
            )
        )
    else:
        read_row.read_at = now
    await db.flush()

    author_map = await _author_name_map(
        db, {ann.created_by} if ann.created_by else set()
    )
    return _to_user_response(
        ann, read_map={ann.id: now}, author_map=author_map
    )


@router.patch("/{announcement_id}", response_model=AnnouncementManageResponse)
async def update_announcement(
    announcement_id: str,
    body: AnnouncementUpdateRequest,
    db: DBSession,
    current_user: InteriorUser,
) -> AnnouncementManageResponse:
    """编辑公告（内部用户）。"""
    result = await db.execute(
        select(SystemAnnouncement).where(SystemAnnouncement.id == announcement_id)
    )
    ann = result.scalar_one_or_none()
    if not ann:
        raise HTTPException(status_code=404, detail="公告不存在")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(ann, field, value)
    await db.flush()
    await db.refresh(ann)

    read_count_result = await db.execute(
        select(func.count(SystemAnnouncementRead.id)).where(
            SystemAnnouncementRead.announcement_id == announcement_id
        )
    )
    read_count = read_count_result.scalar() or 0
    author_map = await _author_name_map(
        db, {ann.created_by} if ann.created_by else set()
    )
    return _to_manage_response(
        ann,
        read_count=read_count,
        total_users=await _total_users(db),
        author_name=author_map.get(ann.created_by) if ann.created_by else None,
    )


@router.delete("/{announcement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_announcement(
    announcement_id: str,
    db: DBSession,
    current_user: InteriorUser,
) -> None:
    """删除公告（内部用户）。已读记录由外键级联删除。"""
    result = await db.execute(
        select(SystemAnnouncement).where(SystemAnnouncement.id == announcement_id)
    )
    ann = result.scalar_one_or_none()
    if not ann:
        raise HTTPException(status_code=404, detail="公告不存在")
    await db.delete(ann)
    await db.flush()
