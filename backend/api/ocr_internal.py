"""私有云内部 OCR 识别接口（X-Internal-Token 鉴权，供私有云后台管理系统调用）。

用途：私有云后台的"基础资料 OCR 自动录入"（营业执照/身份证/证书图片 → 文本）。
复用与 ImageOcrTool 相同的本地 RapidOCR 引擎（无外网依赖）；本接口不写
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

from backend.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ocr-internal"])

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR

        model_dir = str(get_settings().ocr_model_dir)
        _engine = RapidOCR(params={"Global.model_root_dir": model_dir})
    return _engine


def _run_ocr(image_path: Path) -> str:
    engine = _get_engine()
    output = engine(str(image_path))
    if output.txts is None or len(output.txts) == 0:
        return ""
    return "\n".join(output.txts)


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
        text = await asyncio.to_thread(_run_ocr, Path(tmp.name))
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
