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
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from httpx import AsyncClient, ConnectError, HTTPStatusError, RequestError, TimeoutException

from backend.agent.tools.base import ToolResult
from backend.config import get_settings
from backend.services.ocr_image_normalizer import (
    OcrImageNormalizationError,
    normalize_image_for_ocr,
)
from backend.services.usage_context import get_usage_context
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


# ---------------------------------------------------------------------------
# 印章识别节流（2026-10-15）：同一任务内同一枚印章只识别一次。
#
# 背景：印章检测捆绑在 understand_image 上，agent 每看一张图就发一次
# /ocr/v1/seal（10-14 生产实测 81~230 次/审查任务），而同一标书的印章来自
# 固定几枚实体章，重复识别无信息增量；且未勾选 E001 签字盖章检查（默认不勾）
# 的任务也在白烧配额。
#
# 三重门控（_plan_seal_call）：
#   ① BAIDU_SEAL_DETECT_ENABLED 总开关；
#   ② UsageContext.seal_required 任务门控 —— 仅印章相关规则的执行流发起
#     （SubAgentExecutor 按子 agent 的规则文档判定）；无上下文=脚本/测试，
#     保持旧行为不门控；
#   ③ BAIDU_SEAL_MODE 节流模式（见 config.py）。
#
# 收敛状态按 UsageContext.task_id 键控放模块级：BaiduOcrTool 在三处被按次
# 创建（bid_review_agent / document_parser / duplicate_image_evidence），
# 同一审查任务的多个子 agent 不共享实例，状态不能挂 self。
# ---------------------------------------------------------------------------
SEAL_MODE_CONVERGE = "converge"
SEAL_MODE_SIGNATURE_GATE = "signature_gate"

# signature_gate 模式：OCR 文本命中任一关键词才补发印章请求（落款/签署语境）
SEAL_SIGNATURE_KEYWORDS = (
    "签字", "盖章", "公章", "印鉴", "落款", "授权", "承诺",
    "法定代表人", "投标函", "投标保证金", "签署",
)

# 收敛注册表 LRU 上限：每注册表只有指纹字符串集合，极小；64 个任务
# 足够覆盖并发窗口，防长驻 worker 内存无限增长。
_SEAL_REGISTRY_MAX_TASKS = 64


@dataclass
class _SealRegistry:
    """一个任务内的印章指纹收敛状态（模块级，按 task_id 键控 LRU）。"""

    fingerprints: set[str] = field(default_factory=set)
    known: list[str] = field(default_factory=list)  # 已识别印章简述，如 红色圆形「XX」
    consecutive_no_new: int = 0
    calls: int = 0
    converged: bool = False
    converged_reason: str = ""  # window=连续无新指纹 / cap=达调用上限


_seal_registries: "OrderedDict[str, _SealRegistry]" = OrderedDict()


def _seal_registry_for(task_id: str | None) -> _SealRegistry | None:
    """取（或建）任务的印章收敛注册表；无 task_id 返回 None（不做收敛）。"""
    if not task_id:
        return None
    registry = _seal_registries.get(task_id)
    if registry is None:
        registry = _SealRegistry()
        _seal_registries[task_id] = registry
        while len(_seal_registries) > _SEAL_REGISTRY_MAX_TASKS:
            _seal_registries.popitem(last=False)
    else:
        _seal_registries.move_to_end(task_id)
    return registry


def _normalize_seal_text(value: str) -> str:
    """指纹文本规范化：全角→半角、去空白、ASCII 小写（容忍 OCR 噪声）。"""
    out: list[str] = []
    for ch in value or "":
        code = ord(ch)
        if code == 0x3000:
            continue
        if 0xFF01 <= code <= 0xFF5E:
            ch = chr(code - 0xFEE0)
        if ch.isspace():
            continue
        out.append(ch.lower())
    return "".join(out)


def _seal_fingerprint(seal: dict[str, Any]) -> str:
    """印章身份指纹：规范化章面文字 + 编号。

    同一枚实体章在所有页面上指纹一致（业务前提：印章不可能更换）。章面文字
    识别不出时退化为 形状/颜色 —— 不同单位的空章会合并，由调用上限兜底。
    """
    major = _normalize_seal_text(str(seal.get("major") or ""))
    minors = ";".join(
        _normalize_seal_text(str(m)) for m in (seal.get("minor") or []) if m
    )
    if major or minors:
        return f"{major}#{minors}"
    return f"@{seal.get('type') or ''}/{seal.get('color') or ''}"


def _seal_descriptor(seal: dict[str, Any]) -> str:
    """印章简述（去重/收敛提示行用）：红色圆形「章面文字」。"""
    raw_type = str(seal.get("type") or "")
    type_label = SEAL_TYPE_LABELS.get(raw_type, raw_type or "未知类型")
    raw_color = str(seal.get("color") or "")
    color_label = SEAL_COLOR_LABELS.get(raw_color, raw_color)
    desc = f"{color_label}{type_label}"
    if seal.get("major"):
        desc += f"「{seal['major']}」"
    return desc


def _render_seal_line(index: int, seal: dict[str, Any]) -> str:
    """单印章明细行（与 2026-10-14 版输出格式逐字兼容）。"""
    raw_type = str(seal.get("type") or "")
    type_label = SEAL_TYPE_LABELS.get(raw_type, raw_type or "未知类型")
    raw_color = str(seal.get("color") or "")
    color_label = SEAL_COLOR_LABELS.get(raw_color, raw_color)
    prob = seal.get("probability")
    try:
        prob_s = f"，置信度{float(prob):.3f}" if prob is not None else ""
    except (TypeError, ValueError):
        prob_s = ""
    desc = f"{index}. {color_label}{type_label}印章{prob_s}"
    if seal.get("major"):
        desc += f"：章面文字「{seal['major']}」"
    if seal.get("minor"):
        desc += f"，编号/其他文字「{'；'.join(seal['minor'])}」"
    loc = seal.get("location") or {}
    if loc.get("left") is not None:
        desc += (
            f"，位置(左上角x={loc.get('left')},y={loc.get('top')}，"
            f"宽{loc.get('width')}×高{loc.get('height')})"
        )
    return desc


def _signature_gate_matched(ocr_text: str) -> bool:
    return any(kw in (ocr_text or "") for kw in SEAL_SIGNATURE_KEYWORDS)


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
        mode = (settings.baidu_seal_mode or SEAL_MODE_CONVERGE).strip().lower()
        if mode not in (SEAL_MODE_CONVERGE, SEAL_MODE_SIGNATURE_GATE):
            logger.warning(
                "[BaiduOcrTool] 未知 BAIDU_SEAL_MODE=%r，回退 %s",
                settings.baidu_seal_mode,
                SEAL_MODE_CONVERGE,
            )
            mode = SEAL_MODE_CONVERGE
        self._seal_mode = mode
        self._seal_converge_after = max(0, int(settings.baidu_seal_converge_after))
        self._seal_max_calls = max(0, int(settings.baidu_seal_max_calls_per_task))
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
        # 是否发起/何时发起由 _plan_seal_call 三重门控决定（总开关 → 任务门控
        # → 收敛/落款门控）；signature_gate 模式推迟到 OCR 文本返回后再定。
        # 先预热共享 access_token 缓存，避免两个并发请求冷启动时重复取 token；
        # 预热失败则不建印章任务，让下方 OCR 路径以原有方式统一报错。
        usage_ctx = get_usage_context()
        registry = _seal_registry_for(usage_ctx.task_id if usage_ctx else None)
        seal_plan = self._plan_seal_call(registry)
        seal_task: asyncio.Task | None = None
        seal_skip_note: str | None = None
        if seal_plan == "fire":
            seal_task = await self._start_seal_task(image_bytes, registry)
        elif seal_plan == "skip:converged":
            seal_skip_note = self._converged_seal_note(registry)

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

            # signature_gate 模式：OCR 文本命中落款/签署关键词才补发印章请求
            # （印章与 OCR 从并发改为串行，每图多付 ~0.5s 换取逐位置印章语义）
            if seal_plan == "defer" and _signature_gate_matched(ocr_text):
                seal_task = await self._start_seal_task(image_bytes, registry)

            # 合并印章识别结果（失败降级为提示行，不影响文字结果）
            seal_text, seal_meta = await self._collect_seal_result(
                seal_task, registry=registry, skip_note=seal_skip_note
            )
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

    def _plan_seal_call(self, registry: _SealRegistry | None) -> str:
        """决定本次印章识别策略：fire / defer / skip:disabled / skip:converged。"""
        if not (self._seal_enabled and self._seal_endpoint):
            return "skip:disabled"
        ctx = get_usage_context()
        if ctx is not None and not ctx.seal_required:
            return "skip:disabled"
        if registry is None:
            # 无任务上下文（脚本/测试）：保持旧版每图必测行为
            return "fire"
        if self._seal_mode == SEAL_MODE_SIGNATURE_GATE:
            return "defer"
        if registry.converged:
            return "skip:converged"
        if self._seal_max_calls and registry.calls >= self._seal_max_calls:
            registry.converged = True
            registry.converged_reason = "cap"
            return "skip:converged"
        return "fire"

    async def _start_seal_task(
        self, image_bytes: bytes, registry: _SealRegistry | None
    ) -> "asyncio.Task[dict[str, Any]] | None":
        """预热 token 后发起印章识别并发任务；预热失败返回 None（沿用旧行为）。"""
        try:
            await self._get_access_token()
        except Exception:
            return None
        if registry is not None:
            registry.calls += 1
        return asyncio.create_task(
            self._call_seal_api(base64.b64encode(image_bytes).decode("utf-8"))
        )

    def _update_seal_registry(
        self, registry: _SealRegistry | None, seals: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """按指纹去重记账，返回 (新印章列表, 重复印章列表)。无注册表时原样透传。

        无新指纹的图片（含未检出印章）推进收敛计数；印章侧失败不进入本方法，
        不推进也不重置（失败不产出指纹，不该影响收敛判定）。
        """
        if registry is None:
            return seals, []
        new_seals: list[dict[str, Any]] = []
        dup_seals: list[dict[str, Any]] = []
        for seal in seals:
            fingerprint = _seal_fingerprint(seal)
            if fingerprint in registry.fingerprints:
                dup_seals.append(seal)
                continue
            registry.fingerprints.add(fingerprint)
            registry.known.append(_seal_descriptor(seal))
            new_seals.append(seal)
        if new_seals:
            registry.consecutive_no_new = 0
        else:
            registry.consecutive_no_new += 1
            if (
                self._seal_converge_after
                and registry.consecutive_no_new >= self._seal_converge_after
                and not registry.converged
            ):
                registry.converged = True
                registry.converged_reason = "window"
        return new_seals, dup_seals

    def _converged_seal_note(self, registry: _SealRegistry | None) -> str:
        """收敛后跳过印章检测时的提示行。

        必须显式告知 agent「跳过 ≠ 未盖章」，否则 E001 会把无印章信息的
        后续图片误判为未盖章（2026-09-12 同类误判的教训）。
        """
        known = list(registry.known) if registry else []
        if known:
            shown = "；".join(known[:5])
            if len(known) > 5:
                shown += f" 等 {len(known)} 种"
            listing = f"已识别 {len(known)} 种印章：{shown}"
        else:
            listing = "未识别到印章"
        if registry is not None and registry.converged_reason == "cap":
            cause = f"已达单任务印章调用上限（{self._seal_max_calls} 次）"
        else:
            cause = "连续多张图片未检出新印章"
        return (
            f"【印章检测】本卷印章识别已收敛（{listing}；{cause}），"
            "后续图片跳过印章检测；请勿因后续图片未显示印章信息而判定「未盖章」。"
        )

    async def _collect_seal_result(
        self,
        seal_task: "asyncio.Task[dict[str, Any]] | None",
        registry: "_SealRegistry | None" = None,
        skip_note: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """等待并格式化印章识别结果，返回 (文本小节, 结构化元数据)。

        印章侧任何失败都只降级为提示行，绝不影响 OCR 文字结果的成功返回。
        registry 非 None 时附带任务级指纹去重与收敛记账：已识别过的印章不再
        逐字展开（降低 prompt 噪声），并驱动收敛停叫。
        """
        if seal_task is None:
            if skip_note:
                return skip_note, {
                    "enabled": True,
                    "status": "skipped",
                    "reason": "converged",
                    "known_seals": list(registry.known) if registry else [],
                }
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
            # 无印章也是一次"无新指纹"观测，推进收敛计数
            self._update_seal_registry(registry, [])
            return "【印章检测】未检出印章", {"enabled": True, "status": "success", "seals": []}

        seals: list[dict[str, Any]] = []
        for s in seals_raw:
            major = ((s.get("major") or {}).get("words") or "").strip()
            minor_words = []
            for item in s.get("minor") or []:
                words = (item.get("words") or "").strip()
                if words:
                    minor_words.append(words)
            seals.append({
                "type": s.get("type"),
                "color": s.get("color"),
                "major": major,
                "minor": minor_words,
                "probability": s.get("probability"),
                "location": s.get("location") or {},
            })

        was_converged = bool(registry and registry.converged)
        new_seals, dup_seals = self._update_seal_registry(registry, seals)

        lines: list[str] = [f"【印章检测】共检出 {len(seals)} 个印章："]
        for i, seal in enumerate(new_seals, 1):
            lines.append(_render_seal_line(i, seal))
        if dup_seals:
            dup_desc = "；".join(_seal_descriptor(s) for s in dup_seals)
            lines.append(
                f"另有 {len(dup_seals)} 个印章与本卷已识别印章一致（{dup_desc}），不重复展开。"
            )
        if (
            registry is not None
            and registry.converged
            and not was_converged
            and registry.converged_reason == "window"
        ):
            lines.append(
                f"（连续 {self._seal_converge_after} 张图片未检出新印章，本卷印章识别已收敛，"
                "后续图片将跳过印章检测）"
            )
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
