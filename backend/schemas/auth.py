"""Authentication schemas."""

from datetime import datetime
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Schema for login request (JSON body)."""

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UserResponse(BaseModel):
    """Schema for user response."""

    id: str
    username: str
    email: str
    created_at: datetime
    nickname: str | None = None
    city: str | None = None
    company: str | None = None
    bidding_industries: str | None = None
    interior_user: bool = False
    concurrency: int = 2

    model_config = {"from_attributes": True}


class Token(BaseModel):
    """Schema for JWT token response."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    """Schema for refresh token request."""

    refresh_token: str


class TokenData(BaseModel):
    """Schema for decoded JWT token data."""

    user_id: str | None = None
    interior_user: bool = False
    concurrency: int = 2
