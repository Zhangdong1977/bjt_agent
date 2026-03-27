"""Bid Review Agent - extends Mini-Agent with custom tools.

This agent is responsible for comparing tender documents against bid documents
and identifying non-compliant items.
"""

import json
import re
import sys
from pathlib import Path
from typing import Optional

# Add Mini-Agent to path
mini_agent_path = Path(__file__).parent.parent.parent / "Mini-Agent"
if mini_agent_path.exists() and str(mini_agent_path) not in sys.path:
    sys.path.insert(0, str(mini_agent_path))

from mini_agent.agent import Agent as BaseAgent
from mini_agent.llm import LLMClient
from mini_agent.schema import LLMProvider, Message

from backend.config import get_settings
from backend.agent.tools.doc_search import DocSearchTool
from backend.agent.tools.rag_search import RAGSearchTool
from backend.agent.tools.comparator import ComparatorTool
from backend.agent.prompt import SYSTEM_PROMPT

settings = get_settings()


class BidReviewAgent(BaseAgent):
    """Bid review agent that extends Mini-Agent with domain-specific tools."""

    def __init__(
        self,
        project_id: str,
        tender_doc_path: str,
        bid_doc_path: str,
        user_id: str,
        event_callback=None,
        max_steps: int = 100,
    ):
        """Initialize the bid review agent.

        Args:
            project_id: The project ID for organizing workspace
            tender_doc_path: Path to the parsed tender document
            bid_doc_path: Path to the parsed bid document
            user_id: The user ID for workspace organization
            event_callback: Optional callback for SSE event publishing
            max_steps: Maximum number of agent steps
        """
        self.project_id = project_id
        self.tender_doc_path = tender_doc_path
        self.bid_doc_path = bid_doc_path
        self.user_id = user_id
        self.event_callback = event_callback
        self._findings: list[dict] = []

        # Initialize LLM client (MiniMax uses OpenAI protocol)
        llm_client = LLMClient(
            api_key=settings.mini_agent_api_key,
            provider=LLMProvider.OPENAI,  # MiniMax uses OpenAI-compatible API
            api_base=settings.mini_agent_api_base,
            model=settings.mini_agent_model,
        )

        # Initialize tools
        tools = [
            DocSearchTool(tender_doc_path=tender_doc_path, bid_doc_path=bid_doc_path),
            RAGSearchTool(),
            ComparatorTool(),
        ]

        # Set up workspace
        workspace_dir = settings.workspace_path / str(user_id) / str(project_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Initialize base agent
        super().__init__(
            llm_client=llm_client,
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
            workspace_dir=str(workspace_dir),
            max_steps=max_steps,
        )

    def _send_event(self, event_type: str, data: dict) -> None:
        """Send an event via callback if available."""
        if self.event_callback:
            self.event_callback(event_type, data)

    async def run_review(self) -> list[dict]:
        """Run the bid review process with real-time event emission.

        Returns:
            List of findings with requirement, bid content, compliance status, etc.
        """
        # Build the review task description with clear expectations
        task = f"""请审查投标文件相对于招标文件的不符合项。

招标书路径: {self.tender_doc_path}
投标书路径: {self.bid_doc_path}

请严格按照系统提示中的工作流程执行：
1. 读取并提取招标书中的所有要求
2. 查询企业知识库获取相关政策
3. 读取投标书内容
4. 对每个招标要求与投标内容进行比对
5. 识别不符合项并输出结构化的JSON格式结果

重要：最终输出必须包含一个JSON数组，每项代表一个审查发现。"""

        self.add_user_message(task)

        # Send starting event
        self._send_event("progress", {"message": "Starting agent review..."})
        self._send_event("step", {
            "step_number": 1,
            "step_type": "thought",
            "content": "Initializing bid review agent...",
        })

        step_number = 2

        # Run the agent loop manually with event emission
        tool_list = list(self.tools.values())

        while len(self.messages) - 1 < self.max_steps:  # -1 for system message
            # Check for cancellation
            if self.cancel_event and self.cancel_event.is_set():
                break

            # Summarize if needed
            await self._summarize_messages()

            # Get LLM response
            try:
                response = await self.llm.generate(messages=self.messages, tools=tool_list)
            except Exception as e:
                self._send_event("error", {"message": f"LLM error: {str(e)}"})
                break

            # Add assistant message
            assistant_msg = Message(
                role="assistant",
                content=response.content,
                thinking=response.thinking,
                tool_calls=response.tool_calls,
            )
            self.messages.append(assistant_msg)

            # Emit step event
            if response.content:
                self._send_event("step", {
                    "step_number": step_number,
                    "step_type": "thought",
                    "content": str(response.content)[:200],
                })
                step_number += 1

            # Check if task is complete
            if not response.tool_calls:
                break

            # Execute tools with event emission
            for tool_call in response.tool_calls:
                function_name = tool_call.function.name

                # Emit tool call event
                self._send_event("step", {
                    "step_number": step_number,
                    "step_type": "tool_call",
                    "tool_name": function_name,
                    "content": f"Calling {function_name}...",
                })
                step_number += 1

                # Execute tool
                if function_name in self.tools:
                    try:
                        result = await self.tools[function_name].execute(**tool_call.function.arguments)
                        # Emit tool result event
                        result_preview = str(result.content)[:100] if result.success else str(result.error)[:100]
                        self._send_event("step", {
                            "step_number": step_number,
                            "step_type": "observation",
                            "tool_name": function_name,
                            "content": result_preview,
                        })
                        step_number += 1

                        # Add tool message
                        tool_msg = Message(
                            role="tool",
                            content=result.content if result.success else f"Error: {result.error}",
                            tool_call_id=tool_call.id,
                            name=function_name,
                        )
                        self.messages.append(tool_msg)
                    except Exception as e:
                        self._send_event("error", {"message": f"Tool {function_name} failed: {str(e)}"})
                        break
                else:
                    self._send_event("error", {"message": f"Unknown tool: {function_name}"})

        # Extract findings
        findings = self._extract_findings_from_messages()
        if not findings:
            findings = self._parse_findings_from_text(self.messages[-1].content if self.messages else "")

        return findings

    def _extract_findings_from_messages(self) -> list[dict]:
        """Extract structured findings from agent message history.

        Returns:
            List of structured findings if found, empty list otherwise.
        """
        findings = []
        requirement_counter = 1

        for msg in reversed(self.messages):
            if msg.role == "assistant" and msg.content:
                # Try to parse JSON array from content
                parsed = self._try_parse_json(msg.content)
                if parsed and isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and "requirement_key" in item:
                            findings.append(self._normalize_finding(item, requirement_counter))
                            requirement_counter += 1
                # Also check for single JSON object
                elif parsed and isinstance(parsed, dict) and "requirement_key" in parsed:
                    findings.append(self._normalize_finding(parsed, requirement_counter))
                    requirement_counter += 1

        return findings

    def _normalize_finding(self, item: dict, counter: int) -> dict:
        """Normalize a finding to match ReviewResult model.

        Args:
            item: Raw finding dict from agent
            counter: Requirement counter for generating keys

        Returns:
            Normalized finding dict matching ReviewResult schema.
        """
        return {
            "requirement_key": item.get("requirement_key", f"req_{counter:03d}"),
            "requirement_content": item.get("requirement_content", item.get("requirement", "")),
            "bid_content": item.get("bid_content"),
            "is_compliant": item.get("is_compliant", True),
            "severity": item.get("severity") if not item.get("is_compliant", True) else None,
            "location_page": item.get("location_page"),
            "location_line": item.get("location_line"),
            "suggestion": item.get("suggestion"),
            "explanation": item.get("explanation", ""),
        }

    def _try_parse_json(self, content: str) -> Optional[list | dict]:
        """Try to parse JSON from content string.

        Args:
            content: Raw content string

        Returns:
            Parsed JSON object/array or None if parsing fails.
        """
        # Try direct parse
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            pass

        # Try to extract JSON array from markdown
        json_array_match = re.search(r'\[\s*\{.*\}\s*\]', content, re.DOTALL)
        if json_array_match:
            try:
                return json.loads(json_array_match.group(0))
            except json.JSONDecodeError:
                pass

        # Try to extract JSON object
        json_obj_match = re.search(r'\{[^{}]*"[^}]+\}[^{}]*\}', content, re.DOTALL)
        if json_obj_match:
            try:
                return json.loads(json_obj_match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def _parse_findings_from_text(self, text: str) -> list[dict]:
        """Parse findings from unstructured text.

        Args:
            text: Agent output text

        Returns:
            List with single summary finding containing full text.
        """
        # Check for common patterns indicating no issues found
        if any(phrase in text.lower() for phrase in ["完全符合", "全部符合", "无不符合项", "符合所有要求"]):
            return [{
                "requirement_key": "review_pass",
                "requirement_content": "投标文件审查通过",
                "bid_content": None,
                "is_compliant": True,
                "severity": None,
                "location_page": None,
                "location_line": None,
                "suggestion": None,
                "explanation": "投标文件符合招标所有要求",
            }]

        # Return full text as summary
        return [{
            "requirement_key": "review_summary",
            "requirement_content": "投标文件审查结果汇总",
            "bid_content": None,
            "is_compliant": True,
            "severity": "minor",
            "location_page": None,
            "location_line": None,
            "suggestion": None,
            "explanation": text,
        }]
