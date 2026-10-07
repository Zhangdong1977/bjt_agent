"""Stateless image captcha service.

Generates a 4-digit numeric captcha image (PNG) together with a signed,
short-lived token (``captcha_id``) that the login endpoint later verifies.

为什么是无状态签名令牌：
- 不依赖 Redis/DB —— 生产多 worker、重启均安全。
- JWT 载荷里只放 HMAC **摘要**（``hmac(secret, nonce:code)``），绝不放明文验证码。
  因为 JWT 仅做 base64 编码、可被任何人解码，放明文会直接泄露答案。

已知取舍：令牌在 TTL 内可被多次提交（要做到严格一次性需引入服务端状态）。
缓解：TTL 收紧到 ``CAPTCHA_TTL_SECONDS``；真正的认证闸门是外部 ``aiCheckLogin``
（账密），图形验证码仅作二次防御层。
"""

from __future__ import annotations

import base64
import hmac
import io
import logging
import random
import secrets
import time
from dataclasses import dataclass

from jose import ExpiredSignatureError, JWTError, jwt
from PIL import Image, ImageDraw, ImageFont

from backend.config import get_settings

logger = logging.getLogger(__name__)

# 验证码图像尺寸 / 长度 / 有效期
CAPTCHA_WIDTH = 120
CAPTCHA_HEIGHT = 40
CAPTCHA_LENGTH = 4
CAPTCHA_TTL_SECONDS = 180  # 3 分钟
_TOKEN_TYPE = "captcha"

# 数字颜色抖动用色板（深色，白底可读）
_DIGIT_COLORS = [
    (30, 80, 160),    # 蓝
    (200, 30, 40),    # 红
    (30, 120, 70),    # 绿
    (90, 30, 140),    # 紫
    (200, 110, 0),    # 橙
    (20, 20, 20),     # 近黑
]
# 干扰线/噪点颜色
_NOISE_COLORS = [(160, 160, 170), (200, 200, 210), (130, 130, 145)]


@dataclass(frozen=True)
class CaptchaArt:
    """一次生成结果：签名后的 captcha_id + 内联 PNG data URL。"""

    captcha_id: str
    image: str  # data:image/png;base64,...
    expires_in: int


def _make_digest(nonce: str, code: str) -> str:
    """对 ``nonce:code`` 做带应用密钥的 HMAC-SHA256，返回十六进制摘要。"""
    settings = get_settings()
    msg = f"{nonce}:{code}".encode("utf-8")
    return hmac.new(settings.secret_key.encode("utf-8"), msg, "sha256").hexdigest()


def _render_png(code: str) -> bytes:
    """把数字渲染成带旋转/抖动/噪点的 PNG（无需外部字体文件）。

    使用 Pillow 内嵌 FreeType 字体（``load_default(size=...)``），跨平台可移植、
    不依赖系统字体。
    """
    rng = random.Random()  # 仅用于视觉抖动，非密码学用途
    img = Image.new("RGB", (CAPTCHA_WIDTH, CAPTCHA_HEIGHT), "white")
    draw = ImageDraw.Draw(img)

    # 背景噪点
    for _ in range(70):
        x = rng.randint(0, CAPTCHA_WIDTH - 1)
        y = rng.randint(0, CAPTCHA_HEIGHT - 1)
        draw.point((x, y), fill=rng.choice(_NOISE_COLORS))

    # 字号与旋转角取"人眼可辨、机器难OCR"的平衡：2026-10-07 生产案例中用户
    # 连续 4 次输错图形验证码（每次已换新图），28px+±28° 在 30px 字符格里旋转
    # 后互相重叠、难以辨认。收紧到 24px+±18°，防 OCR 仍依赖旋转+噪点+干扰线。
    font = ImageFont.load_default(size=24)
    tile = CAPTCHA_HEIGHT  # 单字画布边长（正方形，便于旋转）
    cell = CAPTCHA_WIDTH / max(len(code), 1)
    for i, ch in enumerate(code):
        # 每个字单独一层，独立旋转后再贴回，提升 OCR 抗识别
        layer = Image.new("RGBA", (tile, tile), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((6, 0), ch, font=font, fill=rng.choice(_DIGIT_COLORS))
        layer = layer.rotate(rng.randint(-18, 18), resample=Image.BILINEAR)

        cx = int(cell * i + cell / 2)
        ox = cx - tile // 2 + rng.randint(-3, 3)
        oy = rng.randint(-2, 4)
        img.paste(layer, (ox, oy), layer)

    # 前景干扰线
    for _ in range(4):
        x1, y1 = rng.randint(0, CAPTCHA_WIDTH), rng.randint(0, CAPTCHA_HEIGHT)
        x2, y2 = rng.randint(0, CAPTCHA_WIDTH), rng.randint(0, CAPTCHA_HEIGHT)
        draw.line((x1, y1, x2, y2), fill=rng.choice(_NOISE_COLORS), width=1)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_captcha() -> CaptchaArt:
    """生成一个新的 4 位数字验证码图像 + 签名 captcha_id。"""
    code = f"{random.randint(0, 10 ** CAPTCHA_LENGTH - 1):0{CAPTCHA_LENGTH}d}"
    nonce = secrets.token_hex(8)
    settings = get_settings()
    now = int(time.time())
    payload = {
        "type": _TOKEN_TYPE,
        "nonce": nonce,
        "digest": _make_digest(nonce, code),
        "iat": now,
        "exp": now + CAPTCHA_TTL_SECONDS,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    png_b64 = base64.b64encode(_render_png(code)).decode("ascii")
    return CaptchaArt(
        captcha_id=token,
        image=f"data:image/png;base64,{png_b64}",
        expires_in=CAPTCHA_TTL_SECONDS,
    )


def verify_captcha_with_reason(
    captcha_id: str | None, code: str | None
) -> tuple[bool, str | None]:
    """校验 captcha_id 与用户输入，返回 ``(是否通过, 失败原因)``。

    原因取值：``missing_input``（空输入）/ ``expired``（令牌过 TTL）/
    ``bad_token``（签名、格式、type、claims 异常）/ ``mismatch``（码不匹配）。
    区分原因是为了让登录/发码端点能给出可行动的提示（"已过期，请看新图"
    vs "输入错误"），并在日志里留下可排查的失败口径。
    """
    if not captcha_id or not code:
        return False, "missing_input"
    settings = get_settings()
    try:
        payload = jwt.decode(
            captcha_id,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
    except ExpiredSignatureError:
        return False, "expired"
    except JWTError:
        return False, "bad_token"

    if payload.get("type") != _TOKEN_TYPE:
        return False, "bad_token"
    nonce = payload.get("nonce")
    digest = payload.get("digest")
    if not nonce or not digest:
        return False, "bad_token"

    expected = _make_digest(nonce, code.strip())
    if hmac.compare_digest(expected, digest):
        return True, None
    return False, "mismatch"


def verify_captcha(captcha_id: str | None, code: str | None) -> bool:
    """兼容包装：仅返回是否通过（历史调用方与测试依赖 bool 语义）。"""
    ok, _ = verify_captcha_with_reason(captcha_id, code)
    return ok
