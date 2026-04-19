"""Merge decider tool for LLM-powered merge decisions."""

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

from mini_agent.llm import LLMClient
from mini_agent.schema import LLMProvider, Message
from backend.agent.tools.base import ToolResult
from mini_agent.tools.base import Tool as BaseTool

from backend.config import get_settings

settings = get_settings()

# Prompt for merge decision
MERGE_DECISION_PROMPT = """你是专业的标书审查结果合并决策专家，负责将新的审查发现与历史发现进行合并。

## 决策原则

**重要**：每次审查都应该被保留，除非新发现与某历史发现**完全重复**（实质内容相同）。

1. **keep** - 保留新发现作为独立条目
   - 新发现与所有现有发现都不重复
   - 新发现提供了新的有价值的信息（不同的位置、补充说明等）

2. **replace** - 用新发现替换某个现有发现
   - 新发现与某现有发现描述的是同一个招标要求
   - 新发现的内容更完整、位置更精确、或 severity 更高

3. **discard** - 丢弃新发现
   - 新发现与某现有发现**实质内容完全相同**
   - is_compliant 相同、severity 相同、explanation 相似、bid_content 相似

## 新发现：
{new_finding}

## 现有发现列表：
{existing_findings}

## 决策指南

- **谨慎 discard**：只有当新发现与现有某个发现"实质相同"时才 discard
- **倾向 keep**：如果有任何疑问，优先选择 keep 而非 discard
- **replace 的使用**：当新旧发现描述同一招标要求但评估结果不同时使用（如一个 compliant 一个不是）

## 输出格式（自然语言，必须包含）：
决策：keep | replace | discard
理由：[详细解释为什么做出这个决策，30-100字]
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
            timeout=60.0,
        )

    async def _call_llm_with_retry(
        self,
        messages: list,
        max_retries: int = 3,
    ) -> Any:
        """Call LLM with retry mechanism.

        Args:
            messages: LLM messages
            max_retries: Maximum retry attempts (default 3)

        Returns:
            LLM response

        Raises:
            Exception: If all retries fail
        """
        last_exception = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[MergeDeciderTool] LLM attempt {attempt}/{max_retries}")
                response = await self._llm_client.generate(messages=messages)
                logger.info(f"[MergeDeciderTool] LLM attempt {attempt} succeeded")
                return response
            except Exception as e:
                last_exception = e
                logger.warning(f"[MergeDeciderTool] LLM attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1)

        logger.error(f"[MergeDeciderTool] All {max_retries} LLM attempts failed")
        raise last_exception

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
        logger.info(f"[MergeDeciderTool.execute] new_finding req_key={new_finding.get('requirement_key')}, existing_findings count={len(existing_findings)}")
        try:
            # Build prompt
            prompt = MERGE_DECISION_PROMPT.format(
                new_finding=json.dumps(new_finding, ensure_ascii=False, indent=2),
                existing_findings=json.dumps(existing_findings, ensure_ascii=False, indent=2),
            )

            messages = [
                Message(role="user", content=prompt),
            ]

            logger.info(f"[MergeDeciderTool.execute] Calling LLM...")
            response = await self._call_llm_with_retry(messages=messages)
            logger.info(f"[MergeDeciderTool.execute] LLM response:\n{response.content[:500]}")

            return ToolResult(
                success=True,
                content=response.content,
                data={"decision": response.content},
            )

        except Exception as e:
            logger.warning(f"[MergeDeciderTool.execute] Error: {e}")
            return ToolResult(success=False, content="", error=str(e))