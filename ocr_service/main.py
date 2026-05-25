"""OCR Microservice - FastAPI application.

Provides a single POST /api/ocr endpoint that accepts base64-encoded images
and returns recognized text via RapidOCR.

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8900
"""

import base64
import logging

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ocr_engine import MAX_IMAGE_SIZE, run_ocr

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# base64 overhead: 4/3 ratio, so 20MB raw ≈ 27MB base64
MAX_BASE64_LENGTH = int(MAX_IMAGE_SIZE * 4 / 3) + 1024

app = FastAPI(title="OCR Service", version="1.0.0")


class OcrRequest(BaseModel):
    image_base64: str = Field(..., description="Base64-encoded image data", max_length=MAX_BASE64_LENGTH)
    image_format: str = Field(default="png", description="Image format: png, jpg, jpeg, webp")


class OcrResponse(BaseModel):
    success: bool
    ocr_text: str = ""
    error: str = ""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/ocr", response_model=OcrResponse)
async def ocr_endpoint(req: OcrRequest):
    try:
        image_bytes = base64.b64decode(req.image_base64)
    except Exception as e:
        logger.error(f"Base64 decode failed: {e}")
        return OcrResponse(success=False, error="Invalid base64 image data")

    fmt = req.image_format.lower().strip(".")
    if fmt not in ("png", "jpg", "jpeg", "webp"):
        fmt = "png"

    try:
        ocr_text = run_ocr(image_bytes, fmt)
        return OcrResponse(success=True, ocr_text=ocr_text)
    except ValueError as e:
        logger.warning(f"OCR validation error: {e}")
        return OcrResponse(success=False, error=str(e))
    except Exception as e:
        logger.error(f"OCR processing failed: {e}")
        return OcrResponse(success=False, error="OCR processing failed")
