"""Mini-Agent tools that proxy read-only calls to the VSTO host."""

from __future__ import annotations

import json
from typing import Any

from backend.services.vsto_tool_broker import VSTO_TOOL_SCHEMAS, VstoToolBroker
from backend.utils.mini_agent_utils import setup_mini_agent_path

setup_mini_agent_path()

from mini_agent.tools.base import Tool, ToolResult  # noqa: E402


class VstoRemoteTool(Tool):
    """One allow-listed VSTO function exposed as an LLM tool."""

    def __init__(self, *, tool_name: str, broker: VstoToolBroker):
        if tool_name not in VSTO_TOOL_SCHEMAS:
            raise ValueError(f"Unknown VSTO tool: {tool_name}")
        self._tool_name = tool_name
        self._broker = broker

    @property
    def name(self) -> str:
        return self._tool_name

    @property
    def description(self) -> str:
        descriptions = {
            "word_get_overview": (
                "读取当前 Word 文档的安全概览和不可变快照信息。只读，不返回整篇正文。"
            ),
            "word_search": (
                "在当前 Word 文档中搜索身份线索或格式要求相关关键词，返回带段落/页码的短证据。"
            ),
            "word_check_format": (
                "根据暗标要求检查 Word 文档格式；只返回结构化格式问题，不修改文档。"
            ),
            "word_scan_identity_clues": (
                "扫描公司名称、人员、联系方式、文件属性等可能暴露投标人身份的线索。只读。"
            ),
            "word_check_page_setup": (
                "逐节检查页面尺寸、A4 和页边距，返回确定性覆盖度和异常 section。只读。"
            ),
            "word_check_headers_footers": (
                "检查所有 section 的页眉、页脚、页码字段和页码集合。只读。"
            ),
            "word_check_blank_pages": (
                "逐页检查没有可见文字或对象的空白页。只读，不删除页面。"
            ),
            "word_check_text_style": (
                "遍历文字范围检查字体、字号、颜色、倾斜和下划线；结果包含覆盖度。只读。"
            ),
            "word_check_paragraph_format": (
                "遍历段落检查固定行距、行距值、段前和段后间距。只读。"
            ),
            "word_check_heading_numbering": (
                "检查 Word 标题样式、标题级别、自动编号文本和编号格式。只读。"
            ),
            "word_check_objects": (
                "盘点图片、图形、文本框、Chart/SmartArt/OLE 等对象并提示视觉内容人工复核。只读。"
            ),
            "word_check_signatures": (
                "检查 Word 数字签名、签名行及 OOXML 签名包。只读。"
            ),
        }
        return descriptions[self._tool_name]

    @property
    def parameters(self) -> dict[str, Any]:
        return json.loads(json.dumps(VSTO_TOOL_SCHEMAS[self._tool_name]))

    async def execute(self, **kwargs) -> ToolResult:
        try:
            result = await self._broker.request(self._tool_name, kwargs)
        except Exception as exc:
            return ToolResult(success=False, content="", error=str(exc))

        if result.get("success"):
            data = result.get("data") or {}
            content = result.get("content") or ""
            if data:
                serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                if not content:
                    content = serialized
                else:
                    # VSTO normally returns the same JSON in both ``data`` and
                    # ``content``.  Compare semantically so whitespace/order
                    # differences do not duplicate a potentially large result
                    # in the model context.
                    try:
                        if json.loads(content) == data:
                            content = serialized
                        else:
                            content = f"{content}\n{serialized}".strip()
                    except (TypeError, ValueError, json.JSONDecodeError):
                        content = f"{content}\n{serialized}".strip()
            return ToolResult(success=True, content=content)
        return ToolResult(
            success=False,
            content="",
            error=str(result.get("error") or "VSTO 工具返回失败"),
        )
