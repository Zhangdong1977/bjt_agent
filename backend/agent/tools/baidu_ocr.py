"""百度云 OCR (accurate_basic 高精度文字识别 + 印章识别) 实现的 understand_image 工具。

当 settings.image_understanding_provider == "baidu" 时，由 BidReviewAgent 用本工具
覆盖 understand_image 工具槽位（与 VolcengineVisionTool 覆盖模式一致），agent 的
提示词与调用流程无需改动。本工具为纯文字识别（OCR），不具备 VLM 语义理解能力；
自 2026-10 起附带百度印章识别（/rest/2.0/ocr/v1/seal），与 OCR 并发调用，
印章检测结果（类型/颜色/位置/章面文字/编号/置信度）合并进返回文本——
用于 E001 签字盖章等检查项，弥补纯 OCR 看不见印章的缺陷。
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
from backend.services.ocr_image_normalizer import (
    OcrImageNormalizationError,
    normalize_image_for_ocr,
)
from mini_agent.tools.base import Tool as BaseTool

logger = logging.getLogger(__name__)

# 百度 accurate_basic 要求 base64 编码后大小不超 ~10MB；源图按 4MB 卡控更稳，
# 非 JPEG/PNG、超限或需要纠正方向的图片会先统一规范化。
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
# 216202 image size error 兜底重试时使用的更紧长边上限。
# normalizer 默认上限是 4096（百度 nominal 上限），但实际部分接近 4096 的图仍被判 size error，
# 兜底重试时压到 2048 再发一次，提高成功率。
IMAGE_SIZE_ERROR_FALLBACK_DIMENSION = 2048

# 印章识别结果字段 → 中文标签（面向 agent 的返回文本）
SEAL_TYPE_LABELS = {
    "circle": "圆形",
    "ellipse": "椭圆形",
    "rectangle": "方形",
    "perforation": "骑缝章",
}
SEAL_COLOR_LABELS = {"red": "红色", "black": "黑色", "blue": "蓝色"}

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
        self._seal_enabled = settings.baidu_seal_detect_enabled
        self._seal_endpoint = settings.baidu_seal_endpoint.rstrip("/")
        self._normalization_cache_dir = settings.workspace_path / ".ocr_image_cache"

    @property
    def name(self) -> str:
        return "understand_image"

    @property
    def description(self) -> str:
        if self._seal_enabled:
            return """【百度OCR图片识别（文字 + 印章检测）】对图片进行高精度OCR文字识别，并同步进行印章检测。

返回内容包含两部分：
1. 图片中的文字内容（按行拼接）；
2. 【印章检测】小节：列出检测到的每个印章的类型（圆形/椭圆形/方形/骑缝章）、颜色（红/黑/蓝）、章面文字、印章编号、置信度和位置；若图片无印章则显示"未检出印章"。

适用：核对证件、证书、合同、业绩证明等图片的文字内容；检查文件是否加盖印章、印章类型与章面单位名称（如投标文件盖章检查）。

限制：不具备通用视觉理解（不能描述版式/场景/人物），不能检测手写签名，不做印章真伪核验；印章章面为环形弯曲文字，识别结果可能有少量错字，与单位名称比对时应模糊对照。

参数：
- 'prompt': 审查关注点（informational，识别结果不据此改变，仅记录审查意图）（必填）
- 'image_source': 本地图片文件路径（必填；JPEG 2000 等格式会自动转换）

返回：图片文字内容 + 【印章检测】结果。"""
        return """【百度OCR图片文字识别】对图片进行高精度OCR文字识别，提取图片中的文字内容。

注意：本工具为纯文字识别（OCR），不具备印章真伪/证书核验/版式说明等语义理解能力，仅返回识别到的文字。
当需要核对投标文件中证件、证书、表格、业绩证明等图片的文字内容时使用。

参数：
- 'prompt': 审查关注点（informational，OCR 不会据此改变识别结果，仅记录审查意图）（必填）
- 'image_source': 本地图片文件路径（必填；JPEG 2000 等格式会自动转换）

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

        file_size = image_path.stat().st_size
        try:
            normalized = await asyncio.to_thread(
                normalize_image_for_ocr,
                image_path,
                cache_dir=self._normalization_cache_dir,
                max_output_bytes=MAX_IMAGE_SIZE_BYTES,
            )
            image_bytes = normalized.path.read_bytes()
        except OcrImageNormalizationError as exc:
            err = f"图片格式转换失败: {exc}"
            logger.error("[BaiduOcrTool] %s", err)
            try:
                from backend.services.usage_recorder import record_ocr_usage

                record_ocr_usage(
                    provider="baidu_ocr",
                    endpoint=self._endpoint,
                    status="error",
                    latency_ms=int((time.perf_counter() - call_start) * 1000),
                    image_size_bytes=file_size,
                    error_code="image_normalization_failed",
                    error_message=str(exc),
                )
            except Exception:
                pass
            return ToolResult(success=False, error=err)

        # 印章识别与文字识别并发执行（同一份规范化图片字节）。
        # 先预热共享 access_token 缓存，避免两个并发请求冷启动时重复取 token；
        # 预热失败则不建印章任务，让下方 OCR 路径以原有方式统一报错。
        seal_task: asyncio.Task | None = None
        if self._seal_enabled and self._seal_endpoint:
            try:
                await self._get_access_token()
            except Exception:
                seal_task = None
            else:
                seal_task = asyncio.create_task(
                    self._call_seal_api(base64.b64encode(image_bytes).decode("utf-8"))
                )

        try:
            b64_image = base64.b64encode(image_bytes).decode("utf-8")

            data = {
                "image": b64_image,
                "detect_direction": "true",  # 自动旋转，适配倾斜的证书扫描件
                "paragraph": "false",
                "probability": "false",
            }
            result = await self._request_api_with_retry(data, self._endpoint)

            # 百度偶发把合法图片判为格式错误。强制转成 RGB JPEG 后仅补发一次，
            # 避免把确定性的格式错误放进网络指数退避重试。
            if str(result.get("error_code") or "") == "216201":
                fallback = await asyncio.to_thread(
                    normalize_image_for_ocr,
                    image_path,
                    cache_dir=self._normalization_cache_dir,
                    max_output_bytes=MAX_IMAGE_SIZE_BYTES,
                    force_reencode=True,
                    prefer_jpeg=True,
                    source_sha256=normalized.source_sha256,
                )
                fallback_bytes = fallback.path.read_bytes()
                if fallback_bytes != image_bytes:
                    image_bytes = fallback_bytes
                    data["image"] = base64.b64encode(image_bytes).decode("utf-8")
                    result = await self._request_api_with_retry(data, self._endpoint)
                    normalized = fallback

            # 百度返回 216202 image size error 时，把图片按更紧的长边上限（2048）
            # 重新降采样后补发一次。覆盖 normalizer 之后的边缘情况：超大图字节虽已压
            # 到 4MB 以内，但像素仍可能触百度上限；或瘦长图比例异常被判 size error。
            # 仅重试一次，失败即按业务错误上报，不进网络重试退避。
            if str(result.get("error_code") or "") == "216202":
                logger.warning(
                    "[BaiduOcrTool] 收到 216202 image size error，按 max_dimension=2048 降采样重试一次: %s",
                    image_path.name,
                )
                fallback = await asyncio.to_thread(
                    normalize_image_for_ocr,
                    image_path,
                    cache_dir=self._normalization_cache_dir,
                    max_output_bytes=MAX_IMAGE_SIZE_BYTES,
                    max_dimension=IMAGE_SIZE_ERROR_FALLBACK_DIMENSION,
                    force_reencode=True,
                    prefer_jpeg=True,
                    source_sha256=normalized.source_sha256,
                )
                fallback_bytes = fallback.path.read_bytes()
                if fallback_bytes != image_bytes:
                    image_bytes = fallback_bytes
                    data["image"] = base64.b64encode(image_bytes).decode("utf-8")
                    result = await self._request_api_with_retry(data, self._endpoint)
                    normalized = fallback

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

            # 合并印章识别结果（失败降级为提示行，不影响文字结果）
            seal_text, seal_meta = await self._collect_seal_result(seal_task)
            content = "\n\n".join(part for part in (ocr_text, seal_text) if part)
            if seal_meta.get("status") == "success":
                logger.info(
                    f"[BaiduOcrTool] 印章检测完成: {image_path.name}, "
                    f"检出 {len(seal_meta.get('seals') or [])} 个印章"
                )

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
                content=content,
                data={
                    "image_path": str(image_path),
                    "ocr_text": ocr_text,
                    "words_result_num": words_num,
                    "provider": "baidu",
                    "source_format": normalized.source_format,
                    "normalized_format": normalized.output_format,
                    "image_converted": normalized.converted,
                    "normalization_cache_hit": normalized.cache_hit,
                    "ocr_image_size_bytes": normalized.output_size_bytes,
                    "seal_detection": seal_meta,
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
        finally:
            # OCR 失败路径不消费印章结果：取消未完成的印章任务，避免悬挂
            if seal_task is not None and not seal_task.done():
                seal_task.cancel()

    async def _call_seal_api(self, b64_image: str) -> dict[str, Any]:
        """调用百度印章识别接口（/rest/2.0/ocr/v1/seal）。

        返回原始响应 dict；业务错误（含 error_code）同样以 dict 返回，由
        _collect_seal_result 决定降级文案。网络级异常向上抛出。
        """
        start = time.perf_counter()
        try:
            result = await self._request_api_with_retry(
                {"image": b64_image}, self._seal_endpoint, api_label="印章识别"
            )
        except TimeoutException:
            self._record_seal_usage("timeout", start, error_message="百度印章识别请求超时")
            raise
        except Exception as exc:
            self._record_seal_usage("error", start, error_message=str(exc))
            raise
        if result.get("error_code"):
            self._record_seal_usage(
                "error", start,
                error_code=str(result.get("error_code")),
                error_message=str(result.get("error_msg")),
            )
        else:
            self._record_seal_usage("success", start, seals_num=len(result.get("result") or []))
        return result

    def _record_seal_usage(
        self, status: str, start: float, *,
        seals_num: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """印章识别用量记录；记录失败不影响主流程。"""
        try:
            from backend.services.usage_recorder import record_ocr_usage

            record_ocr_usage(
                provider="baidu_seal",
                endpoint=self._seal_endpoint,
                status=status,
                latency_ms=int((time.perf_counter() - start) * 1000),
                words_result_num=seals_num,
                error_code=error_code,
                error_message=error_message,
            )
        except Exception:
            pass

    async def _collect_seal_result(
        self, seal_task: "asyncio.Task[dict[str, Any]] | None"
    ) -> tuple[str, dict[str, Any]]:
        """等待并格式化印章识别结果，返回 (文本小节, 结构化元数据)。

        印章侧任何失败都只降级为提示行，绝不影响 OCR 文字结果的成功返回。
        """
        if seal_task is None:
            return "", {"enabled": False}

        try:
            result = await seal_task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            note = f"【印章检测】失败（{exc}），以上仅为OCR文字识别结果"
            logger.warning("[BaiduOcrTool] %s", note)
            return note, {"enabled": True, "status": "error", "error": str(exc)}

        if result.get("error_code"):
            note = (
                f"【印章检测】不可用（百度错误[{result.get('error_code')}]: "
                f"{result.get('error_msg', '未知错误')}），以上仅为OCR文字识别结果"
            )
            logger.warning("[BaiduOcrTool] %s", note)
            return note, {
                "enabled": True,
                "status": "error",
                "error_code": str(result.get("error_code")),
                "error_message": str(result.get("error_msg")),
            }

        seals_raw = result.get("result") or []
        if not seals_raw:
            return "【印章检测】未检出印章", {"enabled": True, "status": "success", "seals": []}

        seals: list[dict[str, Any]] = []
        lines = [f"【印章检测】共检出 {len(seals_raw)} 个印章："]
        for i, s in enumerate(seals_raw, 1):
            raw_type = str(s.get("type") or "")
            type_label = SEAL_TYPE_LABELS.get(raw_type, raw_type or "未知类型")
            raw_color = str(s.get("color") or "")
            color_label = SEAL_COLOR_LABELS.get(raw_color, raw_color)
            major = ((s.get("major") or {}).get("words") or "").strip()
            minor_words = []
            for item in s.get("minor") or []:
                words = (item.get("words") or "").strip()
                if words:
                    minor_words.append(words)
            prob = s.get("probability")
            try:
                prob_s = f"，置信度{float(prob):.3f}" if prob is not None else ""
            except (TypeError, ValueError):
                prob_s = ""
            desc = f"{i}. {color_label}{type_label}印章{prob_s}"
            if major:
                desc += f"：章面文字「{major}」"
            if minor_words:
                desc += f"，编号/其他文字「{'；'.join(minor_words)}」"
            loc = s.get("location") or {}
            if loc.get("left") is not None:
                desc += (
                    f"，位置(左上角x={loc.get('left')},y={loc.get('top')}，"
                    f"宽{loc.get('width')}×高{loc.get('height')})"
                )
            lines.append(desc)
            seals.append({
                "type": s.get("type"),
                "color": s.get("color"),
                "major": major,
                "minor": minor_words,
                "probability": prob,
                "location": loc,
            })
        return "\n".join(lines), {"enabled": True, "status": "success", "seals": seals}

    async def _request_api_with_retry(
        self, data: dict[str, str], endpoint: str, *, api_label: str = "OCR"
    ) -> dict[str, Any]:
        """调用百度 OCR/印章识别接口；仅对瞬时故障重试，最多额外重试 3 次。"""
        total_attempts = OCR_MAX_RETRIES + 1
        last_exception: Exception | None = None
        last_result: dict[str, Any] | None = None

        for attempt in range(1, total_attempts + 1):
            try:
                access_token = await self._get_access_token()
                url = f"{endpoint}?access_token={access_token}"
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
                failure = f"百度{api_label}错误[{error_code}]: {result.get('error_msg', '未知错误')}"
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
                raise RuntimeError(f"百度{api_label}重试失败")

            delay = OCR_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "[BaiduOcrTool] %s 调用第 %d/%d 次失败（%s），%.1fs 后重试",
                api_label,
                attempt,
                total_attempts,
                failure,
                delay,
            )
            await asyncio.sleep(delay)

        raise RuntimeError(f"百度{api_label}重试流程异常结束")

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
