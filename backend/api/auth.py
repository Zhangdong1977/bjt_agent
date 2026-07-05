"""Authentication API routes."""

import logging
import time
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
from backend.schemas.auth import (
    CaptchaResponse,
    LoginRequest,
    RegisterRequest,
    SendSmsRequest,
    Token,
    UserResponse,
    RefreshTokenRequest,
)
from backend.services.captcha_service import generate_captcha, verify_captcha
from backend.middleware.rate_limit import limiter

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])

# 运营台认证端点。生产含 /prod-api，由 nginx 剥离前缀转后端 /aiCheckLogin；
# dev/pre-release 经 .env 的 OPERATE_API_BASE_URL 指 operate-two 直连(无 /prod-api)。
# 与 profile.py(aiCheckUpdatePwd) / operate_coupons.py 同源，统一用 settings.operate_api_base_url。


def external_auth_url() -> str:
    base_url = settings.operate_api_base_url.rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="运营平台认证地址未配置",
        )
    return f"{base_url}/aiCheckLogin"


def _operate_headers() -> dict[str, str]:
    """运营平台 server-to-server 内部接口的共享密钥头。

    与 ``services/operate_recharge.py`` 的 ``_headers()`` 同源：复用
    ``OPERATE_INTERNAL_TOKEN``（须与 operate-two ``document.bocom.internalToken`` 同值）。
    """
    token = settings.operate_internal_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="运营平台内部接口共享密钥未配置",
        )
    return {"X-Internal-Token": token}


def _operate_url(path: str) -> str:
    base_url = settings.operate_api_base_url.rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="运营平台认证地址未配置",
        )
    return f"{base_url}{path}"


def _clarify_sms_error(msg: str | None) -> str | None:
    """把上游透传文案里模糊的「验证码」明确为「短信验证码」。

    send-sms / register 透传这一步，本端图形验证码已校验通过，上游若再返回
    含「验证码」的错误，必指短信验证码。注册页同时有图形 + 短信两个验证码，
    原样透传「验证码有误」会让用户分不清是哪一个出错。已含「短信」或「图形」
    字样的原样返回，避免重复前缀 / 误改。
    """
    if not msg or "短信" in msg or "图形" in msg:
        return msg
    return msg.replace("验证码", "短信验证码")


MOCK_AUTH_ENABLED = False

MOCK_AUTH_RESPONSE = {
    "code": 200,
    "data": {
        "useCheck": 1,
        "interiorUser": 1,
        "concurrency": 2,
    },
}


@router.get("/captcha", response_model=CaptchaResponse)
@limiter.limit("30/minute")
async def get_captcha(request: Request) -> CaptchaResponse:
    """生成登录用 4 位图形验证码：返回签名 captcha_id + 内联 PNG data URL。

    前端用 ``image`` 直接渲染图片，登录时回传 ``captcha_id`` 与用户输入。
    """
    art = generate_captcha()
    return CaptchaResponse(
        captcha_id=art.captcha_id,
        image=art.image,
        expires_in=art.expires_in,
    )


@router.post("/login", response_model=Token)
@limiter.limit("100/minute")
async def login(request: Request, body: LoginRequest, db: DBSession) -> Token:
    """Login via external auth API and issue JWT tokens."""
    # 图形验证码先于外部认证校验，拦截暴力探测、避免无意义的外部调用
    if not verify_captcha(body.captcha_id, body.captcha_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图形验证码错误或已失效",
        )
    if MOCK_AUTH_ENABLED:
        logger.warning("Using mock auth data (external API disabled)")
        ext_result = MOCK_AUTH_RESPONSE["data"]
        use_check = ext_result["useCheck"]
        interior_user = ext_result["interiorUser"] == 1
        concurrency = ext_result["concurrency"]
        external_user_id = None
        external_user_name = body.username
        external_nickname = None
        enterprise_name = None
    else:
        # Call external auth API
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                resp = await client.post(
                    external_auth_url(),
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
        external_nickname = ext_result.get("nickName")
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
    if external_nickname:
        user.nickname = external_nickname
    if enterprise_name and not user.company:
        user.company = enterprise_name
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
        nickname=current_user.nickname,
        city=current_user.city,
        company=current_user.company or current_user.enterprise_name,
        bidding_industries=current_user.bidding_industries,
        interior_user=claims["interior_user"],
        concurrency=claims["concurrency"],
    )


@router.post("/refresh")
async def refresh_token(request: RefreshTokenRequest, db: DBSession) -> Token:
    """Refresh access token using refresh token."""
    from jose import JWTError, jwt

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录状态已失效，请重新登录",
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


@router.post("/send-sms")
@limiter.limit("1/minute")
async def send_sms(request: Request, body: SendSmsRequest) -> dict:
    """站内注册·下发短信验证码。

    先校验图形验证码（防刷短信，复用登录同款 captcha 体系），再转发到运营平台
    ``/aiGetCode``（明文 + X-Internal-Token）。运营平台内部已含 60 秒冷却，
    此处再叠 ``1/minute`` 限流做双保险。
    """
    if not verify_captcha(body.captcha_id, body.captcha_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图形验证码错误或已失效",
        )
    # 手机号脱敏（中间4位），仅用于日志
    masked_phone = body.phone[:3] + "****" + body.phone[7:] if len(body.phone) == 11 else body.phone
    started_at = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=settings.operate_api_timeout_seconds, trust_env=False
        ) as client:
            resp = await client.post(
                _operate_url("/aiGetCode"),
                json={"account": body.phone},
                headers=_operate_headers(),
            )
    except httpx.RequestError as e:
        logger.error(
            "Send-sms upstream request failed: phone=%s timeout=%ss err=%s",
            masked_phone, settings.operate_api_timeout_seconds, e,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="短信服务不可用，请稍后重试",
        )

    data = resp.json()
    if data.get("code") != 200:
        # 透传运营平台消息（如"请X秒后重新发送短信！"）；模糊的「验证码」明确为短信验证码
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_clarify_sms_error(data.get("msg")) or "验证码发送失败",
        )
    logger.info(
        "Send-sms forwarded ok: phone=%s cost=%dms",
        masked_phone, int((time.monotonic() - started_at) * 1000),
    )
    return {"message": data.get("msg", "验证码已发送")}


@router.post("/register")
@limiter.limit("5/minute")
async def register(request: Request, body: RegisterRequest) -> dict:
    """站内注册。先校验图形验证码，再转发到运营平台 ``/aiRegister``。

    运营平台注册成功即置 ``use_check=1``（注册即开通），用户随后可用手机号+密码
    通过 ``/auth/login`` 登录。本端不自动登录——回到登录 tab 手动登录，流程清晰。
    """
    if not verify_captcha(body.captcha_id, body.captcha_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图形验证码错误或已失效",
        )
    payload = {
        # RegisterBody 约定：account=账号(手机号)、username=昵称
        "account": body.phone,
        "username": body.nickname,
        "verificationCode": body.sms_code,
        "password": body.password,
    }
    try:
        async with httpx.AsyncClient(
            timeout=settings.operate_api_timeout_seconds, trust_env=False
        ) as client:
            resp = await client.post(
                _operate_url("/aiRegister"),
                json=payload,
                headers=_operate_headers(),
            )
    except httpx.RequestError as e:
        logger.error("Register upstream request failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="注册服务不可用，请稍后重试",
        )

    data = resp.json()
    if data.get("code") != 200:
        # 透传：手机号已注册 / 短信验证码有误 / 密码强度不足 等；
        # 模糊的「验证码」明确为短信验证码（图形验证码本端已先行校验）
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_clarify_sms_error(data.get("msg")) or "注册失败",
        )
    return {"message": "注册成功，请登录"}
