"""Task-scoped retrieval tools used by technical-bid duplicate agents."""

import json

from backend.utils.mini_agent_utils import setup_mini_agent_path

setup_mini_agent_path()

from mini_agent.tools.base import Tool as BaseTool

from backend.agent.tools.base import ToolResult
from backend.services.duplicate_candidates import DuplicateCandidateService
from backend.services.duplicate_sources import DuplicateSourceIndex


class DuplicateCandidateSearchTool(BaseTool):
    name = "search_duplicate_candidates"
    description = "检索 A/B 技术应标书中相似的段落、表格或数字结构候选对"

    def __init__(self, service: DuplicateCandidateService):
        self.service = service
        super().__init__()

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "规则关键词或检查目标"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        }

    async def execute(self, query: str, limit: int = 30) -> ToolResult:
        items = [item.to_agent_dict() for item in self.service.search(query, limit=limit)]
        return ToolResult(
            success=True,
            content=json.dumps(items, ensure_ascii=False),
            data={"count": len(items), "candidates": items},
        )


class DuplicateCandidateContextTool(BaseTool):
    name = "get_duplicate_context"
    description = "按 candidate_id 获取 A/B 双方前后段落、章节、表格/位置和确定性相似度"

    def __init__(self, service: DuplicateCandidateService):
        self.service = service
        super().__init__()

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
            },
            "required": ["candidate_id"],
        }

    async def execute(self, candidate_id: str) -> ToolResult:
        candidate = self.service.get(candidate_id)
        if candidate is None:
            return ToolResult(success=False, error="候选不存在")
        payload = self.service.get_context(candidate_id, radius=1) or candidate.to_agent_dict()
        return ToolResult(
            success=True,
            content=json.dumps(payload, ensure_ascii=False),
            data=payload,
        )


class DuplicateSourceSearchTool(BaseTool):
    """Retrieve only persisted tender/public source blocks."""

    name = "search_duplicate_sources"
    description = "在任务已上传并固化的招标文件/公共参考资料中检索可追溯来源证据"

    def __init__(self, service: DuplicateSourceIndex):
        self.service = service
        super().__init__()

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要核实的要求、模板或文本"},
                "source_basis": {
                    "type": "string",
                    "enum": ["tender", "public"],
                    "description": "可选来源类型",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "required": ["query"],
        }

    async def execute(
        self,
        query: str,
        source_basis: str | None = None,
        limit: int = 12,
    ) -> ToolResult:
        items = [
            match.to_agent_dict()
            for match in self.service.search(
                query,
                source_basis=source_basis,
                limit=limit,
            )
        ]
        return ToolResult(
            success=True,
            content=json.dumps(items, ensure_ascii=False),
            data={"count": len(items), "sources": items, "warnings": self.service.warnings},
        )


class DuplicateSourceContextTool(BaseTool):
    name = "get_duplicate_source_context"
    description = "按 source_reference_id 读取来源原文、章节、页码、快照 hash 和版本"

    def __init__(self, service: DuplicateSourceIndex):
        self.service = service
        super().__init__()

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"source_reference_id": {"type": "string"}},
            "required": ["source_reference_id"],
        }

    async def execute(self, source_reference_id: str) -> ToolResult:
        payload = self.service.get_context(source_reference_id, radius=1)
        if payload is None:
            return ToolResult(success=False, error="来源证据不存在或未在本次检索结果中")
        return ToolResult(
            success=True,
            content=json.dumps(payload, ensure_ascii=False),
            data=payload,
        )


__all__ = [
    "DuplicateCandidateContextTool",
    "DuplicateCandidateSearchTool",
    "DuplicateSourceContextTool",
    "DuplicateSourceSearchTool",
]
