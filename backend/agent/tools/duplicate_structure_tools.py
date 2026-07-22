"""Restricted structure tools for one duplicate-check document pair."""

from __future__ import annotations

import json
from pathlib import Path

from backend.agent.tools.base import ToolResult
from backend.services.duplicate_structure import read_section
from backend.utils.mini_agent_utils import setup_mini_agent_path

setup_mini_agent_path()
from mini_agent.tools.base import Tool as BaseTool


class DuplicateDocumentTocTool(BaseTool):
    def __init__(self, documents: dict[str, dict]):
        self.documents = documents

    @property
    def name(self) -> str:
        return "get_duplicate_document_toc"

    @property
    def description(self) -> str:
        return "获取当前查重文档对中指定文档的真实章节目录和结构质量。"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {
            "document_id": {"type": "string"},
        }, "required": ["document_id"]}

    async def execute(self, document_id: str, **_) -> ToolResult:
        document = self.documents.get(document_id)
        if not document:
            return ToolResult(success=False, content="", error="文档不属于当前查重文档对")
        path = document.get("structure_index_path")
        if not path or not Path(path).is_file():
            data = {"quality": "none", "sections": []}
        else:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        return ToolResult(success=True, content=json.dumps(data, ensure_ascii=False), data=data)


class DuplicateSectionContentTool(BaseTool):
    def __init__(self, documents: dict[str, dict]):
        self.documents = documents

    @property
    def name(self) -> str:
        return "get_duplicate_section_content"

    @property
    def description(self) -> str:
        return "按文档ID和章节ID分页读取章节原文，仅允许访问当前文档对。"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {
            "document_id": {"type": "string"},
            "section_id": {"type": "string"},
            "cursor": {"type": "integer", "minimum": 0, "default": 0},
            "max_chars": {"type": "integer", "minimum": 200, "maximum": 20000, "default": 12000},
        }, "required": ["document_id", "section_id"]}

    async def execute(self, document_id: str, section_id: str, cursor: int = 0,
                      max_chars: int = 12000, **_) -> ToolResult:
        document = self.documents.get(document_id)
        if not document:
            return ToolResult(success=False, content="", error="文档不属于当前查重文档对")
        index_path = document.get("structure_index_path")
        if not index_path or not Path(index_path).is_file():
            return ToolResult(success=False, content="", error="文档没有可靠章节索引")
        structure = json.loads(Path(index_path).read_text(encoding="utf-8"))
        section = next((s for s in structure.get("sections", []) if s["section_id"] == section_id), None)
        if not section:
            return ToolResult(success=False, content="", error="章节不存在")
        content = read_section(document["parsed_path"], section)
        cursor = max(0, cursor)
        max_chars = min(20000, max(200, max_chars))
        page = content[cursor:cursor + max_chars]
        next_cursor = cursor + len(page) if cursor + len(page) < len(content) else None
        data = {"section": section, "content": page, "cursor": cursor, "next_cursor": next_cursor}
        return ToolResult(success=True, content=page, data=data)
