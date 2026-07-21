"""百度云 OCR (accurate_basic 高精度文字识别) 实现的 understand_image 工具。

当 settings.image_understanding_provider == "baidu" 时，由 BidReviewAgent 用本工具
覆盖 understand_image 工具槽位（与 VolcengineVisionTool 覆盖模式一致），agent 的
提示词与调用流程无需改动。本工具为纯文字识别（OCR），不具备 VLM 语义理解能力。
"""

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any

from httpx import AsyncClient, ConnectError, HTTPStatusError, RequestError, TimeoutException

from backend.agent.tools.base import ToolResult
from backend.config import get_settings
from mini_agent.tools.base import Tool as BaseTool

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
# 百度 accurate_basic 要求 base64 编码后大小不超 ~10MB；源图按 4MB 卡控更稳，
# 超限则尝试 Pillow 压缩。
MAX_IMAGE_SIZE_BYTES = 4 * 1024 * 1024
TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
# access_token 提前刷新余量（秒）
TOKEN_REFRESH_MARGIN = 60.0
# 首次调用失败后最多再重试 3 次，即单次工具调用最多请求 4 次。
OCR_MAX_RETRIES = 3
OCR_RETRY_BASE_DELAY_SECONDS = 1.0
RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
RETRYABLE_BAIDU_ERROR_CODES = {"1", "2", "18", "282000"}
TOKEN_ERROR_CODES = {"110", "111"}

# 模块级 access_token 缓存：{api_key: (token, expires_at_epoch)}
# Celery prefork worker 为独立进程，各进程独立缓存；同进程跨事件循环复用 dict 安全。
# 冷启动并发获取属良性竞争（token 有效期 30 天，后写覆盖，无害），故不引入跨循环 lock。
_token_cache: dict[str, tuple[str, float]] = {}


class BaiduOcrTool(BaseTool):
    """百度云 OCR 文字识别工具，作为 understand_image 的可切换实现。"""

    def __init__(self):
        super().__init__()
        settings = get_settings()
        self._api_key = settings.baidu_ocr_api_key.strip()
        self._secret_key = settings.baidu_ocr_secret_key.strip()
        self._app_id = settings.baidu_ocr_app_id.strip()  # accurate_basic 不强依赖，保留备用
        self._endpoint = settings.baidu_ocr_endpoint.rstrip("/")

    @property
    def name(self) -> str:
        return "understand_image"

    @property
    def description(self) -> str:
        return """【百度OCR图片文字识别】对图片进行高精度OCR文字识别，提取图片中的文字内容。

注意：本工具为纯文字识别（OCR），不具备印章真伪/证书核验/版式说明等语义理解能力，仅返回识别到的文字。
当需要核对应标书中证件、证书、表格、业绩证明等图片的文字内容时使用。

参数：
- 'prompt': 审查关注点（informational，OCR 不会据此改变识别结果，仅记录审查意图）（必填）
- 'image_source': 本地图片文件路径（必填，支持 png/jpg/jpeg/webp/bmp/tiff）

返回：图片中识别到的文字内容（按行拼接）。"""

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "审查关注点（OCR 不使用，仅记录审查意图）",
                },
                "image_source": {
                    "type": "string",
                    "description": "待识别的本地图片文件路径",
                },
            },
            "required": ["prompt", "image_source"],
        }

    async def execute(self, prompt: str = None, image_source: str = None, **kwargs) -> ToolResult:
        call_start = time.perf_counter()  # 用量记录：OCR 调用耗时
        if not prompt:
            return ToolResult(success=False, error="Missing required parameter: prompt")
        if not image_source:
            return ToolResult(success=False, error="Missing required parameter: image_source")

        if not self._api_key or not self._secret_key:
            return ToolResult(
                success=False,
                error="百度OCR凭证未配置：请在 .env 中设置 BAIDU_OCR_API_KEY 与 BAIDU_OCR_SECRET_KEY",
            )

        # 兼容 MCP 工具的 @ 前缀
        source = image_source.lstrip("@")
        image_path = Path(source)
        if not image_path.exists():
            return ToolResult(success=False, error=f"图片文件不存在: {image_path}")

        ext = image_path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return ToolResult(
                success=False,
                error=f"不支持的图片格式: {ext}。支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            )

        file_size = image_path.stat().st_size
        image_bytes = image_path.read_bytes()

        # 超限尝试压缩（Pillow 为可选依赖）
        if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
            compressed = _maybe_compress(image_bytes)
            if compressed is not None:
                logger.info(
                    f"[BaiduOcrTool] 图片过大({file_size / 1024:.0f}KB)，已压缩至 "
                    f"{len(compressed) / 1024:.0f}KB"
                )
                image_bytes = compressed
            else:
                size_mb = len(image_bytes) / (1024 * 1024)
                return ToolResult(
                    success=False,
                    error=(
                        f"图片过大: {size_mb:.1f}MB，超过百度OCR上限(约{MAX_IMAGE_SIZE_BYTES // (1024 * 1024)}MB)。"
                        "请缩小图片或安装 Pillow 以自动压缩。"
                    ),
                )

        try:
            b64_image = base64.b64encode(image_bytes).decode("utf-8")

            data = {
                "image": b64_image,
                "detect_direction": "true",  # 自动旋转，适配倾斜的证书扫描件
                "paragraph": "false",
                "probability": "false",
            }
            result = await self._request_ocr_with_retry(data)

            # 百度错误响应含 error_code（成功时无此字段或为 0）
            if result.get("error_code"):
                err = f"百度OCR错误[{result.get('error_code')}]: {result.get('error_msg', '未知错误')}"
                logger.error(f"[BaiduOcrTool] {err}")
                # 用量记录：百度业务错误（失败可见但不计费）
                try:
                    from backend.services.usage_recorder import record_ocr_usage
                    record_ocr_usage(provider="baidu_ocr", endpoint=self._endpoint, status="error",
                                     latency_ms=int((time.perf_counter() - call_start) * 1000),
                                     image_size_bytes=file_size,
                                     error_code=str(result.get("error_code")),
                                     error_message=str(result.get("error_msg")))
                except Exception:
                    pass
                return ToolResult(success=False, error=err)

            words_result = result.get("words_result") or []
            ocr_text = "\n".join(item.get("words", "") for item in words_result)
            words_num = result.get("words_result_num", len(words_result))

            logger.info(
                f"[BaiduOcrTool] 识别完成: {image_path.name} ({file_size / 1024:.1f}KB), "
                f"{words_num} 行文字"
            )
            # 用量记录：success（含识别行数 + 图片大小 + 预估费用）
            try:
                from backend.services.usage_recorder import record_ocr_usage
                record_ocr_usage(provider="baidu_ocr", endpoint=self._endpoint, status="success",
                                 latency_ms=int((time.perf_counter() - call_start) * 1000),
                                 words_result_num=words_num, image_size_bytes=file_size)
            except Exception:
                pass
            return ToolResult(
                success=True,
                content=ocr_text,
                data={
                    "image_path": str(image_path),
                    "ocr_text": ocr_text,
                    "words_result_num": words_num,
                    "provider": "baidu",
                },
            )

        except TimeoutException:
            logger.error("[BaiduOcrTool] 百度OCR请求超时(60s)")
            try:
                from backend.services.usage_recorder import record_ocr_usage
                record_ocr_usage(provider="baidu_ocr", endpoint=self._endpoint, status="timeout",
                                 latency_ms=int((time.perf_counter() - call_start) * 1000),
                                 image_size_bytes=file_size, error_message="百度OCR请求超时(60s)")
            except Exception:
                pass
            return ToolResult(success=False, error="百度OCR请求超时(60s)")
        except ConnectError as e:
            err = f"无法连接百度OCR服务: {e}"
            logger.error(f"[BaiduOcrTool] {err}")
            try:
                from backend.services.usage_recorder import record_ocr_usage
                record_ocr_usage(provider="baidu_ocr", endpoint=self._endpoint, status="error",
                                 latency_ms=int((time.perf_counter() - call_start) * 1000),
                                 image_size_bytes=file_size, error_message=err)
            except Exception:
                pass
            return ToolResult(success=False, error=err)
        except HTTPStatusError as e:
            err = f"百度OCR HTTP错误[{e.response.status_code}]"
            logger.error(f"[BaiduOcrTool] {err}")
            try:
                from backend.services.usage_recorder import record_ocr_usage
                record_ocr_usage(provider="baidu_ocr", endpoint=self._endpoint, status="error",
                                 latency_ms=int((time.perf_counter() - call_start) * 1000),
                                 image_size_bytes=file_size,
                                 error_code=str(e.response.status_code), error_message=err)
            except Exception:
                pass
            return ToolResult(success=False, error=err)
        except Exception as e:
            err = f"百度OCR调用异常: {e}"
            logger.error(f"[BaiduOcrTool] {err}")
            try:
                from backend.services.usage_recorder import record_ocr_usage
                record_ocr_usage(provider="baidu_ocr", endpoint=self._endpoint, status="error",
                                 latency_ms=int((time.perf_counter() - call_start) * 1000),
                                 image_size_bytes=file_size, error_message=err)
            except Exception:
                pass
            return ToolResult(success=False, error=err)

    async def _request_ocr_with_retry(self, data: dict[str, str]) -> dict[str, Any]:
        """调用百度 OCR；仅对瞬时故障重试，最多额外重试 3 次。"""
        total_attempts = OCR_MAX_RETRIES + 1
        last_exception: Exception | None = None
        last_result: dict[str, Any] | None = None

        for attempt in range(1, total_attempts + 1):
            try:
                access_token = await self._get_access_token()
                url = f"{self._endpoint}?access_token={access_token}"
                async with AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        url,
                        data=data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    response.raise_for_status()
                    result = response.json()

                error_code = str(result.get("error_code") or "")
                if not error_code:
                    return result

                if error_code in TOKEN_ERROR_CODES:
                    # 缓存 token 失效时强制刷新，下一次尝试会重新获取。
                    _token_cache.pop(self._api_key, None)

                if error_code not in RETRYABLE_BAIDU_ERROR_CODES | TOKEN_ERROR_CODES:
                    return result
                failure = f"百度OCR错误[{error_code}]: {result.get('error_msg', '未知错误')}"
                last_result = result
                last_exception = None
            except HTTPStatusError as exc:
                if exc.response.status_code not in RETRYABLE_HTTP_STATUS_CODES:
                    raise
                failure = f"HTTP {exc.response.status_code}"
                last_exception = exc
                last_result = None
            except (RequestError, ValueError) as exc:
                failure = str(exc) or type(exc).__name__
                last_exception = exc
                last_result = None

            if attempt == total_attempts:
                if last_result is not None:
                    return last_result
                if last_exception is not None:
                    raise last_exception
                raise RuntimeError("百度OCR重试失败")

            delay = OCR_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "[BaiduOcrTool] OCR 调用第 %d/%d 次失败（%s），%.1fs 后重试",
                attempt,
                total_attempts,
                failure,
                delay,
            )
            await asyncio.sleep(delay)

        raise RuntimeError("百度OCR重试流程异常结束")

    async def _get_access_token(self) -> str:
        """获取（带缓存的）百度 access_token。冷启动并发获取为良性竞争，后写覆盖。"""
        cached = _token_cache.get(self._api_key)
        if cached and cached[1] > time.time() + TOKEN_REFRESH_MARGIN:
            return cached[0]

        params = {
            "grant_type": "client_credentials",
            "client_id": self._api_key,
            "client_secret": self._secret_key,
        }
        async with AsyncClient(timeout=30.0) as client:
            response = await client.post(TOKEN_URL, params=params)
            response.raise_for_status()
            payload = response.json()

        if "access_token" not in payload:
            err = payload.get("error_description") or payload.get("error") or "未知错误"
            raise RuntimeError(f"获取百度access_token失败: {err}")

        token = payload["access_token"]
        expires_in = float(payload.get("expires_in", 2592000))
        _token_cache[self._api_key] = (token, time.time() + expires_in)
        logger.info(f"[BaiduOcrTool] 已获取百度access_token，有效期 {int(expires_in / 86400)} 天")
        return token


def _maybe_compress(image_bytes: bytes) -> bytes | None:
    """超限图片尝试用 Pillow 压缩为 JPEG；Pillow 不可用或失败时返回 None。"""
    try:
        from io import BytesIO

        from PIL import Image
    except ImportError:
        return None

    try:
        img = Image.open(BytesIO(image_bytes))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        max_edge = 2000
        w, h = img.size
        scale = max_edge / max(w, h)
        if scale < 1:
            img = img.resize((int(w * scale), int(h * scale)))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"[BaiduOcrTool] Pillow 压缩失败: {e}")
        return None
