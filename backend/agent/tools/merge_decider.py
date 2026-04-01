"""Merge decider tool for LLM-powered merge decisions."""

import json
from typing import Any

from mini_agent.llm import LLMClient
from mini_agent.schema import LLMProvider, Message
from mini_agent.tools.base import Tool as BaseTool, ToolResult

from backend.config import get_settings

settings = get_settings()

# Prompt for merge decision
MERGE_DECISION_PROMPT = """你是专业的标书审查结果合并决策专家。

给定一个新发现和一系列已存在的发现，请判断：
1. keep - 保留新发现（与现有所有发现都不重复或新发现信息更丰富）
2. replace - 用新发现替换某个现有发现（新发现更完整/severity更高/位置更精确）
3. discard - 丢弃新发现（与某现有发现重复且没有提供任何新信息）

## 新发现：
{new_finding}

## 现有发现列表：
{existing_findings}

## 输出格式（自然语言，必须包含）：
决策：keep | replace | discard
理由：[详细解释为什么做出这个决策，50-200字]
替换key：[如果决策是replace，填入被替换的 requirement_key，否则填"无"]
"""


class MergeDeciderTool(BaseTool):
    """Tool for LLM to decide merge strategy between findings."""

    def __init__(self):
        """Initialize the merge decider tool."""
        super().__init__()
        self._llm_client = LLMClient(
            api_key=settings.mini_agent_api_key,
            provider=LLMProvider.OPENAI,
            api_base=settings.mini_agent_api_base,
            model=settings.mini_agent_model,
        )

    @property
    def name(self) -> str:
        return "decide_merge"

    @property
    def description(self) -> str:
        return """决定新发现与历史发现的合并策略。

输入 JSON:
- new_finding: 新发现的完整信息
- existing_findings: 现有发现列表

输出自然语言决策，包含：决策、理由、替换key"""

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "new_finding": {
                    "type": "object",
                    "description": "新发现的完整信息",
                },
                "existing_findings": {
                    "type": "array",
                    "description": "现有发现列表",
                },
            },
            "required": ["new_finding", "existing_findings"],
        }

    async def execute(
        self,
        new_finding: dict,
        existing_findings: list[dict],
    ) -> ToolResult:
        """Execute merge decision via LLM.

        Args:
            new_finding: 新发现
            existing_findings: 现有发现列表

        Returns:
            ToolResult with natural language decision
        """
        try:
            # Build prompt
            prompt = MERGE_DECISION_PROMPT.format(
                new_finding=json.dumps(new_finding, ensure_ascii=False, indent=2),
                existing_findings=json.dumps(existing_findings, ensure_ascii=False, indent=2),
            )

            messages = [
                Message(role="user", content=prompt),
            ]

            response = await self._llm_client.generate(messages=messages)

            return ToolResult(
                success=True,
                content=response.content,
                data={"decision": response.content},
            )

        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))