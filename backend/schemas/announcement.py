"""Pydantic schemas for system announcements."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


Severity = Literal["info", "important", "urgent"]


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class AnnouncementCreateRequest(BaseModel):
    """发布系统公告（内部用户）。"""

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    severity: Severity = "info"
    is_active: bool = True
    published_at: datetime | None = None  # 不传则用创建时间
    expires_at: datetime | None = None


class AnnouncementUpdateRequest(BaseModel):
    """编辑系统公告（内部用户）。所有字段可选。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1)
    severity: Severity | None = None
    is_active: bool | None = None
    published_at: datetime | None = None
    expires_at: datetime | None = None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PublicAnnouncementResponse(BaseModel):
    """公开（未登录可读）公告——登录页跑马灯用。仅暴露公开展示字段。"""

    id: str
    title: str
    content: str
    severity: str
    published_at: datetime

    model_config = {"from_attributes": True}


class AnnouncementResponse(BaseModel):
    """已登录用户视角的公告（带当前用户的已读状态）。"""

    id: str
    title: str
    content: str
    severity: str
    is_active: bool
    published_at: datetime
    expires_at: datetime | None = None
    created_by: str | None = None
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
    # 当前用户维度
    is_read: bool = False
    read_at: datetime | None = None

    model_config = {"from_attributes": True}


class AnnouncementManageResponse(BaseModel):
    """管理端视角（内部用户）：含已读统计，不含个人已读状态。"""

    id: str
    title: str
    content: str
    severity: str
    is_active: bool
    published_at: datetime
    expires_at: datetime | None = None
    created_by: str | None = None
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
    read_count: int = 0
    total_users: int = 0

    model_config = {"from_attributes": True}


class AnnouncementListResponse(BaseModel):
    """公告列表 + 计数（user 端用）。"""

    items: list[AnnouncementResponse]
    total: int
    unread_count: int


class UnreadCountResponse(BaseModel):
    """未读计数（顶栏角标用）。"""

    unread_count: int


class MarkAllReadResponse(BaseModel):
    """一键全部已读返回。"""

    marked_count: int
