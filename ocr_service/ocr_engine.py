"""OCR engine wrapper using RapidOCR with lazy initialization."""

import logging
import os
import tempfile
import threading

logger = logging.getLogger(__name__)

_engine = None
_lock = threading.Lock()

MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20 MB


def _get_engine():
    """Lazy-init RapidOCR engine (thread-safe double-checked locking)."""
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from rapidocr import RapidOCR

                _engine = RapidOCR()
                logger.info("RapidOCR engine loaded")
    return _engine


def run_ocr(image_bytes: bytes, image_format: str = "png") -> str:
    """Run OCR on image bytes. Returns recognized text joined by newlines."""
    if len(image_bytes) > MAX_IMAGE_SIZE:
        raise ValueError(f"Image too large: {len(image_bytes)} bytes (max {MAX_IMAGE_SIZE})")

    suffix = f".{image_format}" if image_format else ".png"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(image_bytes)
        tmp.flush()
        tmp.close()

        ocr = _get_engine()
        output = ocr(tmp.name)
    finally:
        os.unlink(tmp.name)

    if output.txts is None or len(output.txts) == 0:
        return ""

    return "\n".join(output.txts)
