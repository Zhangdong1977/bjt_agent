"""Profile schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


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
