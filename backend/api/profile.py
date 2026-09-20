"""User profile API routes."""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
import httpx

from backend.api.deps import (
    DBSession,
    CurrentUser,
    get_token_claims,
    oauth2_scheme,
)
from backend.api.open import hash_api_key
from backend.config import get_settings
from backend.middleware.rate_limit import limiter
from backend.models import ApiKey
from backend.schemas.profile import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyItem,
    PasswordChangeRequest,
    ProfileResponse,
    ProfileUpdateRequest,
)
from backend.utils.time_utils import utc_now

router = APIRouter(prefix="/profile", tags=["Profile"])
settings = get_settings()

# 每用户同时可持有的有效（未吊销）API Key 数量
MAX_ACTIVE_API_KEYS_PER_USER = 3


@router.get("/me", response_model=ProfileResponse)
async def get_profile(
    request: Request,
    current_user: CurrentUser,
) -> ProfileResponse:
    token = await oauth2_scheme(request)
    claims = get_token_claims(token)
    return ProfileResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        nickname=current_user.nickname,
        city=current_user.city,
        company=current_user.company or current_user.enterprise_name,
        bidding_industries=current_user.bidding_industries,
        created_at=current_user.created_at,
        interior_user=claims["interior_user"],
        concurrency=claims["concurrency"],
    )


@router.put("/me", response_model=ProfileResponse)
async def update_profile(
    request: Request,
    body: ProfileUpdateRequest,
    db: DBSession,
    current_user: CurrentUser,
) -> ProfileResponse:
    current_user.nickname = body.nickname
    current_user.city = body.city
    current_user.company = body.company
    current_user.bidding_industries = body.bidding_industries
    await db.flush()
    token = await oauth2_scheme(request)
    claims = get_token_claims(token)
    return ProfileResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        nickname=current_user.nickname,
        city=current_user.city,
        company=current_user.company or current_user.enterprise_name,
        bidding_industries=current_user.bidding_industries,
        created_at=current_user.created_at,
        interior_user=claims["interior_user"],
        concurrency=claims["concurrency"],
    )


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    current_user: CurrentUser,
) -> None:
    if body.new_password != body.confirm_new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="两次输入的新密码不一致")

    base_url = settings.operate_api_base_url.rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="运营平台密码服务未配置",
        )
    url = f"{base_url}/aiCheckUpdatePwd"
    try:
        async with httpx.AsyncClient(timeout=settings.operate_api_timeout_seconds, trust_env=False) as client:
            response = await client.post(
                url,
                json={
                    "username": current_user.username,
                    "password": body.old_password,
                    "newPassword": body.new_password,
                    "confirmPassword": body.confirm_new_password,
                },
            )
            response.raise_for_status()
            data = response.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="运营平台密码服务不可用，请稍后重试",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="运营平台密码服务返回异常",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="运营平台密码服务返回异常",
        ) from exc

    if data.get("code") not in (0, 200, "0", "200"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=data.get("msg") or "密码修改失败",
        )
    return None


# ---------------------------------------------------------------------------
# API Key 管理（Skill/开放接入）：供个人中心「API Key」页签调用。
# 明文 Key 只在创建响应里出现一次；列表仅返回前缀；吊销即失效。
# ---------------------------------------------------------------------------


@router.get("/api-keys", response_model=list[ApiKeyItem])
async def list_api_keys(
    db: DBSession,
    current_user: CurrentUser,
) -> list[ApiKeyItem]:
    """List the current user's API keys (active first, newest first)."""
    rows = (
        await db.execute(
            select(ApiKey)
            .where(ApiKey.user_id == current_user.id)
            .order_by(ApiKey.revoked_at.is_not(None), ApiKey.created_at.desc())
        )
    ).scalars().all()
    return [ApiKeyItem.model_validate(row) for row in rows]


@router.post(
    "/api-keys", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("10/minute")
async def create_api_key(
    request: Request,
    db: DBSession,
    current_user: CurrentUser,
    body: ApiKeyCreateRequest | None = None,
) -> ApiKeyCreatedResponse:
    """Create a new API key. The plaintext key is returned exactly once."""
    active_count = (
        await db.execute(
            select(ApiKey).where(
                ApiKey.user_id == current_user.id, ApiKey.revoked_at.is_(None)
            )
        )
    ).scalars()
    if len(active_count.all()) >= MAX_ACTIVE_API_KEYS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"有效 API Key 数量已达上限（{MAX_ACTIVE_API_KEYS_PER_USER} 个），请先吊销不再使用的 Key",
        )

    raw_key = f"bjt_live_{secrets.token_hex(20)}"
    row = ApiKey(
        user_id=current_user.id,
        name=(body.name if body and body.name and body.name.strip() else "default").strip(),
        key_prefix=raw_key[:12],
        key_hash=hash_api_key(raw_key),
        max_active_tasks=1,
    )
    db.add(row)
    await db.flush()
    return ApiKeyCreatedResponse(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        max_active_tasks=row.max_active_tasks,
        created_at=row.created_at,
        api_key=raw_key,
    )


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """Revoke one of the current user's API keys (takes effect immediately)."""
    row = (
        await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    ).scalar_one_or_none()
    if row is None or row.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API Key 不存在"
        )
    if row.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="该 API Key 已被吊销"
        )
    row.revoked_at = utc_now()
    await db.flush()
    return {"msg": "API Key 已吊销，使用该 Key 的调用将立即失效"}
