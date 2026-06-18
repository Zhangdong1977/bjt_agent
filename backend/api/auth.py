"""Authentication API routes."""

import logging
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from backend.api.deps import (
    DBSession,
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_password_hash,
    get_token_claims,
    oauth2_scheme,
)
from backend.config import get_settings
from backend.models import User
from backend.schemas.auth import LoginRequest, Token, UserResponse, RefreshTokenRequest
from backend.middleware.rate_limit import limiter

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])

EXTERNAL_AUTH_URL = "https://aibjt.com:40060/prod-api/aiCheckLogin"


MOCK_AUTH_ENABLED = False

MOCK_AUTH_RESPONSE = {
    "code": 200,
    "data": {
        "useCheck": 1,
        "interiorUser": 1,
        "concurrency": 2,
    },
}


@router.post("/login", response_model=Token)
@limiter.limit("100/minute")
async def login(request: Request, body: LoginRequest, db: DBSession) -> Token:
    """Login via external auth API and issue JWT tokens."""
    if MOCK_AUTH_ENABLED:
        logger.warning("Using mock auth data (external API disabled)")
        ext_result = MOCK_AUTH_RESPONSE["data"]
        use_check = ext_result["useCheck"]
        interior_user = ext_result["interiorUser"] == 1
        concurrency = ext_result["concurrency"]
        external_user_id = None
        external_user_name = body.username
        enterprise_name = None
    else:
        # Call external auth API
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    EXTERNAL_AUTH_URL,
                    json={"username": body.username, "password": body.password},
                )
        except httpx.RequestError as e:
            logger.error(f"External auth API request failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="认证服务不可用，请稍后重试",
            )

        ext_data = resp.json()

        # External API returned error
        if ext_data.get("code") != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ext_data.get("msg", "认证失败"),
            )

        ext_result = ext_data.get("data", {})
        use_check = ext_result.get("useCheck", 0)
        interior_user = ext_result.get("interiorUser", 0) == 1
        concurrency = ext_result.get("concurrency") or settings.max_sub_agent_concurrency
        # 运营台 aiCheckLogin 扩展返回的归属维度（userId/userName/enterpriseName），
        # 落库到本地 users + JWT claims，供 ai_usage_records 归属使用
        external_user_id = ext_result.get("userId")  # sys_user.user_id (bigint)
        external_user_name = ext_result.get("userName")
        enterprise_name = ext_result.get("enterpriseName")

    # Check permission
    if use_check != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="您没有使用检查功能的权限",
        )

    # Find or create local user
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            username=body.username,
            email=f"{body.username}@aibjt",
            password_hash=get_password_hash("external_auth"),
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)

    # 同步运营台返回的归属维度（每次登录刷新，企业名/userId 可能变动）
    user.external_user_id = external_user_id
    user.enterprise_name = enterprise_name
    user.interior_user = interior_user
    await db.flush()

    # Create tokens with claims
    token_data = {
        "sub": user.id,
        "interior_user": interior_user,
        "concurrency": concurrency,
        # 用量归属透传（可选，主要是落库后由 SubAgentExecutor 反查本地 User）
        "external_user_id": external_user_id,
        "enterprise_name": enterprise_name,
        "user_name": external_user_name or body.username,
    }

    access_token = create_access_token(
        data=token_data,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )
    refresh_token = create_refresh_token(
        data=token_data,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )

    return Token(access_token=access_token, refresh_token=refresh_token)


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    request: Request,
    db: DBSession,
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Get current user information with auth claims."""
    token = await oauth2_scheme(request)
    claims = get_token_claims(token)

    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        created_at=current_user.created_at,
        interior_user=claims["interior_user"],
        concurrency=claims["concurrency"],
    )


@router.post("/refresh")
async def refresh_token(request: RefreshTokenRequest, db: DBSession) -> Token:
    """Refresh access token using refresh token."""
    from jose import JWTError, jwt

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            request.refresh_token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")

        if user_id is None or token_type != "refresh":
            raise credentials_exception

        # Propagate claims from refresh token
        interior_user = payload.get("interior_user", False)
        concurrency = payload.get("concurrency", 2)
    except JWTError:
        raise credentials_exception

    # Verify user exists
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception

    token_data = {
        "sub": user.id,
        "interior_user": interior_user,
        "concurrency": concurrency,
    }

    access_token = create_access_token(
        data=token_data,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )
    new_refresh_token = create_refresh_token(
        data=token_data,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )

    return Token(access_token=access_token, refresh_token=new_refresh_token)
