"""API dependency injection."""

from datetime import datetime, timedelta
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models import get_db_session, User
from backend.schemas.auth import TokenData

settings = get_settings()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8")
    )


def get_password_hash(password: str) -> str:
    """Hash a password."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_prefix}/auth/login")


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


def create_refresh_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT refresh token with longer expiration."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    """Get the current authenticated user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        token_data = TokenData(user_id=user_id)
    except JWTError:
        raise credentials_exception

    from sqlalchemy import select
    result = await db.execute(select(User).where(User.id == token_data.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    # Attach interior_user claim (from JWT) so downstream permission checks can
    # bypass project ownership for internal/admin users (e.g. experience dashboard
    # viewing any user's project). The flag comes from a signed token, not the
    # request body, so it cannot be spoofed by the client.
    setattr(user, "_interior_user", bool(payload.get("interior_user", False)))
    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get the current active user."""
    return current_user


def get_token_claims(token: str) -> dict:
    """Decode JWT and extract interior_user and concurrency claims."""
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    return {
        "interior_user": payload.get("interior_user", False),
        "concurrency": payload.get("concurrency", 2),
    }


def is_interior_user(user) -> bool:
    """Whether the given user may access any project (internal/admin view).

    The flag is attached to the User object in get_current_user from the JWT
    claim `interior_user`. Use this in permission helpers to bypass project
    ownership for read-only / review endpoints exposed to internal users.
    """
    return bool(getattr(user, "_interior_user", False))


# Type aliases for cleaner dependency injection
CurrentUser = Annotated[User, Depends(get_current_active_user)]
DBSession = Annotated[AsyncSession, Depends(get_db_session)]


async def require_interior_user(current_user: CurrentUser) -> User:
    """Reject non-internal users with 403.

    用于暴露跨用户 / 经验仪表盘等内部数据的端点。挂在路由依赖上，进入
    函数体前即完成拦截，调用方无需再重复内联校验。
    """
    if not is_interior_user(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅内部用户可访问经验仪表盘",
        )
    return current_user


InteriorUser = Annotated[User, Depends(require_interior_user)]
