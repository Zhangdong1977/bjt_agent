"""Volcengine Doubao Vision tool for image understanding."""

import base64
import logging
import time
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from backend.agent.tools.base import ToolResult
from backend.config import get_settings
from mini_agent.tools.base import Tool as BaseTool

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MIME_TYPE_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


class VolcengineVisionTool(BaseTool):
    """Analyze images using Volcengine Doubao Vision API."""

    def __init__(self):
        super().__init__()
        settings = get_settings()
        self._api_key = settings.volcengine_api_key
        self._api_base = settings.volcengine_api_base.rstrip("/")
        self._model = settings.volcengine_model
        self._client: AsyncOpenAI | None = None

    @property
    def name(self) -> str:
        return "understand_image"

    @property
    def description(self) -> str:
        return """Analyze an image using vision AI. Returns a text description of the image content.

Input should be a JSON object with:
- 'prompt': What to analyze or describe in the image (required)
- 'image_source': Path to the image file (required, supports local files)

Returns the AI's analysis of the image based on the prompt."""

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Description of what to analyze in the image",
                },
                "image_source": {
                    "type": "string",
                    "description": "Path to the image file to analyze",
                },
            },
            "required": ["prompt", "image_source"],
        }

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._api_base,
                timeout=60.0,
            )
        return self._client

    async def execute(self, prompt: str = None, image_source: str = None, **kwargs) -> ToolResult:
        if not prompt:
            return ToolResult(success=False, error="Missing required parameter: prompt")
        if not image_source:
            return ToolResult(success=False, error="Missing required parameter: image_source")

        # Strip @ prefix (MCP tool accepted this format)
        source = image_source.lstrip("@")

        image_path = Path(source)
        if not image_path.exists():
            return ToolResult(
                success=False,
                error=f"Image file not found: {image_path}",
            )

        ext = image_path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return ToolResult(
                success=False,
                error=f"Unsupported image format: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            )

        file_size = image_path.stat().st_size
        if file_size > MAX_IMAGE_SIZE_BYTES:
            size_mb = file_size / (1024 * 1024)
            return ToolResult(
                success=False,
                error=f"Image file too large: {size_mb:.1f}MB. Maximum: 10MB",
            )

        started = time.perf_counter()
        try:
            image_bytes = image_path.read_bytes()
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            mime_type = MIME_TYPE_MAP[ext]
            data_url = f"data:{mime_type};base64,{b64_data}"

            client = self._get_client()
            response = await client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                max_tokens=4096,
            )

            content = response.choices[0].message.content
            logger.info(
                f"[VolcengineVisionTool] Analyzed image: {image_path.name} "
                f"({file_size / 1024:.1f}KB), response length: {len(content)}"
            )

            usage = getattr(response, "usage", None)
            usage_data = {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            }
            try:
                from backend.services.usage_recorder import record_vision_usage

                record_vision_usage(
                    provider="volcengine_vision",
                    model=self._model,
                    status="success",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    image_size_bytes=file_size,
                    **usage_data,
                )
            except Exception:
                logger.exception("[VolcengineVisionTool] failed to schedule usage record")
            return ToolResult(success=True, content=content, data=usage_data)

        except Exception as e:
            error_msg = f"Volcengine vision API error: {e}"
            logger.error(f"[VolcengineVisionTool] {error_msg}")
            try:
                from backend.services.usage_recorder import record_vision_usage

                record_vision_usage(
                    provider="volcengine_vision",
                    model=self._model,
                    status="error",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    image_size_bytes=file_size,
                    error_message=str(e),
                )
            except Exception:
                logger.exception("[VolcengineVisionTool] failed to schedule failed usage record")
            return ToolResult(success=False, error=error_msg)
