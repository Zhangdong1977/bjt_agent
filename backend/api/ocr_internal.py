"""私有云内部 OCR 识别接口（X-Internal-Token 鉴权，供私有云后台管理系统调用）。

用途：私有云后台的"基础资料 OCR 自动录入"与"素材库图片识别"（营业执照/身份证/
证书图片 → 文本）。引擎与公有云 bjt-agent 保持一致：百度云 accurate_basic
（复用 BaiduOcrTool 的 token 缓存/图片规范化/重试实现）；本接口不写
ai_usage_records、不调 record_ocr_usage —— 次数记账由调用方（私有云后台）
直写其 pc_usage_record，避免双计。
"""

import asyncio
import base64
import binascii
import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.agent.tools.baidu_ocr import MAX_IMAGE_SIZE_BYTES, BaiduOcrTool
from backend.config import get_settings
from backend.services.ocr_image_normalizer import normalize_image_for_ocr

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ocr-internal"])

_tool: Optional[BaiduOcrTool] = None


def _get_tool() -> BaiduOcrTool:
    global _tool
    if _tool is None:
        _tool = BaiduOcrTool()
    return _tool


async def _run_ocr(image_path: Path) -> str:
    tool = _get_tool()
    # 与 BaiduOcrTool.execute 相同的前置规范化（格式转换/4MB 压缩/方向纠正）
    normalized = await asyncio.to_thread(
        normalize_image_for_ocr,
        image_path,
        cache_dir=tool._normalization_cache_dir,
        max_output_bytes=MAX_IMAGE_SIZE_BYTES,
    )
    b64_image = base64.b64encode(normalized.path.read_bytes()).decode("utf-8")
    data = {
        "image": b64_image,
        "detect_direction": "true",
        "paragraph": "false",
        "probability": "false",
    }
    result = await tool._request_ocr_with_retry(data)
    if result.get("error_code"):
        raise HTTPException(
            status_code=502,
            detail=f"百度OCR错误[{result.get('error_code')}]: {result.get('error_msg', '未知错误')}",
        )
    words_result = result.get("words_result") or []
    return "\n".join(item.get("words", "") for item in words_result)


class OcrRecognizeRequest(BaseModel):
    image_base64: str
    filename: Optional[str] = None


class OcrRecognizeResponse(BaseModel):
    text: str
    lines: int


@router.post("/ocr/recognize", response_model=OcrRecognizeResponse)
async def ocr_recognize(request: Request, body: OcrRecognizeRequest) -> OcrRecognizeResponse:
    token = get_settings().operate_internal_token
    if not token or request.headers.get("X-Internal-Token") != token:
        raise HTTPException(status_code=403, detail="无权访问")

    raw = body.image_base64 or ""
    if "," in raw[:64] and raw[:5] in ("data:", "iVBOR",):
        # 兼容 data URL 前缀
        raw = raw.split(",", 1)[1]
    if not raw:
        raise HTTPException(status_code=400, detail="image_base64 不能为空")
    try:
        image_bytes = base64.b64decode(raw)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="image_base64 不是合法的 base64")
    if len(image_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片超过 20MB 上限")

    suffix = ".png"
    if body.filename and Path(body.filename).suffix:
        suffix = Path(body.filename).suffix[:8]
    tmp = tempfile.NamedTemporaryFile(prefix="pc_ocr_", suffix=suffix, delete=False)
    try:
        tmp.write(image_bytes)
        tmp.close()
        text = await _run_ocr(Path(tmp.name))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("[ocr-internal] recognize failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"OCR 识别失败: {exc}")
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    lines = [line for line in text.splitlines() if line.strip()]
    return OcrRecognizeResponse(text=text, lines=len(lines))
