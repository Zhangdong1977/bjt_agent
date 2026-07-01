"""User profile API routes."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
import httpx

from backend.api.deps import (
    DBSession,
    CurrentUser,
    get_token_claims,
    oauth2_scheme,
)
from backend.config import get_settings
from backend.schemas.profile import PasswordChangeRequest, ProfileResponse, ProfileUpdateRequest

router = APIRouter(prefix="/profile", tags=["Profile"])
settings = get_settings()


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
