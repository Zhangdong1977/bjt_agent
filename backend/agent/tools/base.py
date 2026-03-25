"""Base tool class for custom tools."""

from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    """Tool execution result."""

    success: bool
    content: str = ""
    error: str | None = None
    data: dict[str, Any] | None = None
