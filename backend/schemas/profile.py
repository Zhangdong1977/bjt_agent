"""Profile schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProfileResponse(BaseModel):
    id: str
    username: str
    email: str
    nickname: str | None = None
    city: str | None = None
    company: str | None = None
    bidding_industries: str | None = None
    created_at: datetime
    interior_user: bool = False
    concurrency: int = 2


class ProfileUpdateRequest(BaseModel):
    nickname: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    company: str | None = Field(default=None, max_length=200)
    bidding_industries: str | None = Field(default=None, max_length=1000)


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=64)
    confirm_new_password: str = Field(..., min_length=8, max_length=64)


class ApiKeyItem(BaseModel):
    """API Key 列表项——明文 Key 只在创建响应里出现一次，列表只回前缀。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    key_prefix: str
    max_active_tasks: int = 1
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)


class ApiKeyCreatedResponse(ApiKeyItem):
    api_key: str
