"""Task-scoped retrieval tools used by technical-bid duplicate agents."""

import json

from backend.utils.mini_agent_utils import setup_mini_agent_path

setup_mini_agent_path()

from mini_agent.tools.base import Tool as BaseTool

from backend.agent.tools.base import ToolResult
from backend.services.duplicate_candidates import DuplicateCandidateService


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
    description = "按 candidate_id 获取 A/B 双方可追溯证据和确定性相似度"

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
        payload = candidate.to_agent_dict()
        return ToolResult(
            success=True,
            content=json.dumps(payload, ensure_ascii=False),
            data=payload,
        )
