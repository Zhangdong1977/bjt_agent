"""Authentication schemas."""

from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    """Schema for login request (JSON body).

    ``captcha_id`` 来自 ``GET /auth/captcha`` 返回的签名令牌，``captcha_code`` 是
    用户看图输入的 4 位数字。二者必填，登录时先校验图形验证码再走外部认证。
    """

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    captcha_id: str = Field(..., min_length=1)
    captcha_code: str = Field(..., min_length=1)


class SendSmsRequest(BaseModel):
    """站内注册·下发短信验证码请求。

    复用登录同款图形验证码（``captcha_id``/``captcha_code``）防刷短信；
    ``phone`` 为接收短信码的手机号。后端校验图形验证码后转发到运营平台
    ``/aiGetCode``。
    """

    phone: str = Field(..., min_length=1)
    captcha_id: str = Field(..., min_length=1)
    captcha_code: str = Field(..., min_length=1)

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        # 与运营平台 /aiGetCode 的正则一致：中国大陆手机号
        import re

        if not re.match(r"^1[3-9]\d{9}$", v):
            raise ValueError("手机号格式不正确")
        return v


class RegisterRequest(BaseModel):
    """站内注册请求。后端校验图形验证码后转发到运营平台 ``/aiRegister``。

    运营平台字段约定（与 /aiRegister 对齐）：phone=手机号(作账号)、nickname=昵称、
    sms_code=短信验证码、password/confirm_password=明文密码（server-to-server，不走 RSA）。
    """

    phone: str = Field(..., min_length=1)
    sms_code: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    confirm_password: str = Field(..., min_length=1)
    nickname: str = Field(..., min_length=1)
    captcha_id: str = Field(..., min_length=1)
    captcha_code: str = Field(..., min_length=1)

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        import re

        if not re.match(r"^1[3-9]\d{9}$", v):
            raise ValueError("手机号格式不正确")
        return v

    @model_validator(mode="after")
    def _check_password_match(self) -> "RegisterRequest":
        if self.password != self.confirm_password:
            raise ValueError("两次输入的密码不一致")
        return self


class CaptchaResponse(BaseModel):
    """Schema for ``GET /auth/captcha`` response (签名令牌 + 内联 PNG data URL)。"""

    captcha_id: str
    image: str
    expires_in: int


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
