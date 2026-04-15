"""Bid Review Agent - extends Mini-Agent with custom tools.

This agent is responsible for comparing tender documents against bid documents
and identifying non-compliant items.
"""

import asyncio
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
from mini_agent.tools.mcp_loader import load_mcp_tools_async, cleanup_mcp_connections

from backend.config import get_settings
from backend.agent.tools.doc_search import DocSearchTool
from backend.agent.tools.rag_search import RAGSearchTool
from backend.agent.tools.comparator import ComparatorTool
from backend.agent.tools import MergeDeciderTool
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
        """Initialize the bid review agent (synchronous part).

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
        # Store tool results for persistence via _record_agent_step
        # Use list to preserve order and allow multiple calls to same tool
        self._tool_results: list[dict] = []
        # Track the starting index for each step's tool results
        self._tool_results_step_start: int = 0

        # Initialize LLM client (MiniMax uses OpenAI protocol)
        llm_client = LLMClient(
            api_key=settings.mini_agent_api_key,
            provider=LLMProvider.OPENAI,  # MiniMax uses OpenAI-compatible API
            api_base=settings.mini_agent_api_base,
            model=settings.mini_agent_model,
        )
        self.llm_client = llm_client  # Store for _call_llm_with_retry

        # Initialize tools
        tools = [
            DocSearchTool(tender_doc_path=tender_doc_path, bid_doc_path=bid_doc_path),
            RAGSearchTool(user_id=user_id),
            ComparatorTool(),
            MergeDeciderTool(),
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

    async def initialize(self):
        """Async initialization - load MCP tools (call after construction)."""
        # Load MCP tools (MiniMax-Coding-Plan-MCP)
        mcp_config_path = Path(__file__).parent.parent / "mcp.json"
        if mcp_config_path.exists():
            try:
                mcp_tools = await load_mcp_tools_async(str(mcp_config_path))
                for tool in mcp_tools:
                    self.tools[tool.name] = tool
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Failed to load MCP tools: {e}")

    def _send_event(self, event_type: str, data: dict) -> None:
        """Send an event via callback if available."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[BidReviewAgent._send_event] type={event_type}, data_keys={list(data.keys())}, callback_exists={self.event_callback is not None}")
        if self.event_callback:
            try:
                self.event_callback(event_type, data)
                logger.info(f"[BidReviewAgent._send_event] Successfully sent event type={event_type}")
            except Exception as e:
                logger.error(f"[BidReviewAgent._send_event] Failed to send event: {e}")

    async def run_review(self) -> list[dict]:
        """Run the bid review process.

        Returns:
            List of findings with requirement, bid content, compliance status, etc.
        """
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[BidReviewAgent.run_review] Starting, tender={self.tender_doc_path}, bid={self.bid_doc_path}")

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

        # Run the agent loop manually
        tool_list = list(self.tools.values())
        logger.info(f"[BidReviewAgent.run_review] tools count: {len(tool_list)}, messages: {len(self.messages)}")

        # Use a dedicated step counter instead of len(messages)-1 to ensure
        # step numbers are sequential starting from 1
        step_counter = 1

        try:
            while len(self.messages) - 1 < self.max_steps:  # -1 for system message
                # Check for cancellation
                if self.cancel_event and self.cancel_event.is_set():
                    break

                # Summarize if needed
                await self._summarize_messages()

                logger.info(f"[BidReviewAgent.run_review] Calling LLM.generate() at step {step_counter}")
                # Get LLM response (max_tokens=8192 ensures full JSON output without truncation)
                response = await self.llm.generate(messages=self.messages, tools=tool_list, max_tokens=8192)
                logger.info(f"[BidReviewAgent.run_review] LLM response received, content length: {len(response.content) if response.content else 0}, tool_calls: {len(response.tool_calls) if response.tool_calls else 0}")

                # Add assistant message
                assistant_msg = Message(
                    role="assistant",
                    content=response.content,
                    thinking=response.thinking,
                    tool_calls=response.tool_calls,
                )
                self.messages.append(assistant_msg)

                # Check if task is complete
                if not response.tool_calls:
                    # Send final response event without tool calls
                    raw_content = response.content
                    if raw_content is None:
                        content_preview = ""
                    elif isinstance(raw_content, str):
                        content_preview = raw_content[:200]
                    else:
                        content_preview = str(raw_content)[:200]
                    self._send_event("step", {
                        "step_number": step_counter,
                        "step_type": "thought",
                        "tool_name": None,
                        "content": content_preview,
                        "tool_calls": [],
                        "tool_results": [],
                    })
                    break

                # Execute tools and collect results
                for tool_call in response.tool_calls:
                    function_name = tool_call.function.name

                    # Validate arguments before executing
                    if tool_call.function.arguments is None:
                        tool_call.function.arguments = {}

                    # Execute tool first
                    if function_name in self.tools:
                        result = await self.tools[function_name].execute(**tool_call.function.arguments)

                        # Store tool result for persistence (append to list, don't overwrite)
                        self._tool_results.append({
                            "id": tool_call.id,
                            "name": function_name,
                            "status": "success" if result.success else "error",
                            "content": result.content if result.success and result.content else None,
                            "error": result.error if not result.success else None,
                            "count": getattr(result, 'count', None),
                        })

                        # Add tool message
                        tool_msg_content = result.content if result.success else f"Error: {result.error}"
                        if tool_msg_content is None:
                            tool_msg_content = ""
                        tool_msg = Message(
                            role="tool",
                            content=tool_msg_content,
                            tool_call_id=tool_call.id,
                            name=function_name,
                        )
                        self.messages.append(tool_msg)
                    else:
                        # Add error tool message for unknown tool
                        tool_msg = Message(
                            role="tool",
                            content=f"Error: Unknown tool {function_name}",
                            tool_call_id=tool_call.id,
                            name=function_name,
                        )
                        self.messages.append(tool_msg)

                # Send single step event with content + tool_calls + tool_results
                raw_content = response.content
                if raw_content is None:
                    content_preview = ""
                elif isinstance(raw_content, str):
                    content_preview = raw_content[:200]
                else:
                    content_preview = str(raw_content)[:200]
                step_type = "observation" if response.tool_calls else "thought"
                # Only send tool_results that belong to this step (from _tool_results_step_start onwards)
                step_tool_results = self._tool_results[self._tool_results_step_start:] if self._tool_results else []
                self._send_event("step", {
                    "step_number": step_counter,
                    "step_type": step_type,
                    "tool_name": None,
                    "content": content_preview,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                        for tc in response.tool_calls
                    ] if response.tool_calls else [],
                    "tool_results": step_tool_results,
                })
                # Update tracker for next step
                self._tool_results_step_start = len(self._tool_results)
                step_counter += 1

        except Exception as e:
            logger.exception(f"[BidReviewAgent.run_review] Exception in run_review loop: {e}")
            return []

        # Extract findings
        findings = self._extract_findings_from_messages()
        if not findings:
            findings = self._parse_findings_from_text(self.messages[-1].content if self.messages else "")

        logger.info(f"[BidReviewAgent.run_review] Completed with {len(findings)} findings")
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
                            normalized = self._normalize_finding(item, requirement_counter)
                            if normalized:
                                findings.append(normalized)
                                requirement_counter += 1
                # Also check for single JSON object
                elif parsed and isinstance(parsed, dict) and "requirement_key" in parsed:
                    normalized = self._normalize_finding(parsed, requirement_counter)
                    if normalized:
                        findings.append(normalized)
                        requirement_counter += 1

        return findings

    def _normalize_finding(self, item: dict, counter: int) -> Optional[dict]:
        """Normalize a finding to match ReviewResult model.

        Args:
            item: Raw finding dict from agent
            counter: Requirement counter for generating keys

        Returns:
            Normalized finding dict matching ReviewResult schema, or None if invalid.
        """
        # Validate required fields - if requirement_content is missing or empty,
        # this is likely a malformed JSON fragment (e.g., just {"explanation": "..."})
        requirement_content = item.get("requirement_content") or item.get("requirement")
        if not requirement_content:
            return None

        # Reject JSON fragments masquerading as requirement_content
        # A proper requirement is natural language text, not a JSON field declaration
        # Examples of malformed content:
        #   '"explanation": "..."'
        #   '"requirement": "..."'
        #   '{"explanation": "...", ...}'
        requirement_str = str(requirement_content).strip()
        if (
            # Starts with " followed by a JSON field name pattern
            (requirement_str.startswith('"') and '":' in requirement_str) or
            # Looks like a JSON object (starts with { and contains JSON fields)
            (requirement_str.startswith('{') and '"' in requirement_str and ':' in requirement_str)
        ):
            return None

        # Reject table headers and pipe-delimited content (e.g., "要求 | 符合状态 | 严重程度")
        # Table headers typically have multiple | delimiters with short values
        if '|' in requirement_str:
            # Count pipe characters - table headers usually have 2+ pipes
            pipe_count = requirement_str.count('|')
            if pipe_count >= 2:
                return None
            # Also reject if it's primarily pipe-delimited structure
            parts = requirement_str.split('|')
            if len(parts) >= 3 and all(len(p.strip()) < 20 for p in parts):
                return None

        # Reject if requirement looks like a table separator line (dashes, equals, etc.)
        if re.match(r'^[-=]{3,}$', requirement_str):
            return None

        # Validate that this looks like a finding, not a random JSON object
        # A proper finding should have either is_compliant or severity
        if "is_compliant" not in item and "severity" not in item:
            # Check if it has at least some finding-related fields
            if not any(key in item for key in ["bid_content", "explanation", "suggestion", "location_page", "location_line"]):
                return None

        # Determine severity with validation
        is_compliant = item.get("is_compliant", True)
        severity = item.get("severity")

        if not is_compliant and not severity:
            # Non-compliant but missing severity - default to "major"
            severity = "major"
        elif not is_compliant and severity:
            # Validate severity against requirement type
            requirement_str = str(requirement_content).lower()
            # Check for optional requirement language
            is_optional_req = any(kw in requirement_str for kw in ["可", "可选", "可给予补充", "可以"])
            # Check for mandatory requirement language
            is_mandatory_req = any(kw in requirement_str for kw in ["必须", "应当", "以", "为准", "强制", "严禁", "不得"])

            if is_optional_req and not is_mandatory_req:
                # Optional requirement should be minor if not provided
                if severity not in ["minor"]:
                    # Override to minor for optional requirements
                    severity = "minor"

        return {
            "requirement_key": item.get("requirement_key") or f"req_{counter:03d}",
            "requirement_content": requirement_content,
            "bid_content": item.get("bid_content"),
            "is_compliant": is_compliant,
            "severity": severity if not is_compliant else None,
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
        if not content:
            return None

        # Try direct parse
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code blocks (```json ... ``` or ``` ... ```)
        # Pattern matches fenced code blocks with optional json/lang specifier
        json_code_block_match = re.search(
            r'```(?:json)?\s*\n?(.*?)\n?```',
            content,
            re.DOTALL | re.IGNORECASE
        )
        if json_code_block_match:
            json_str = json_code_block_match.group(1).strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        # Try to extract JSON array from content (handles multi-line arrays)
        # Look for [...] ) pattern with balanced brackets
        json_array_match = re.search(r'\[\s*\{.*\}\s*\]', content, re.DOTALL)
        if json_array_match:
            try:
                return json.loads(json_array_match.group(0))
            except json.JSONDecodeError:
                pass

        # Try to extract JSON object (handles multi-line objects)
        json_obj_match = re.search(r'\{[^{}]*"[^"]+"\s*:[^{}]*\}', content, re.DOTALL)
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
            List of structured findings parsed from text.
        """
        findings = []
        requirement_counter = 1

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

        # Try to extract structured findings from text patterns

        # Pattern 1: Look for numbered items like "1. 要求: xxx" or "1. xxx"
        numbered_items = re.findall(
            r'(?:^|\n)\s*(?:\d+[.、)]\s*)(.+(?:\n(?!\s*(?:\d+[.、)]\s*)[^　\s]).*)*)',
            text,
            re.MULTILINE
        )

        # Pattern 2: Look for lines with compliance keywords
        compliance_lines = []
        for line in text.split('\n'):
            line_lower = line.lower().strip()
            if any(kw in line_lower for kw in ['符合', '不合规', '不满足', '违规', '缺失', '缺少', '未提供']):
                compliance_lines.append(line.strip())

        # Pattern 3: Extract JSON objects - improved to handle nested braces
        # First try to find complete JSON arrays or objects
        json_array_match = re.search(r'\[\s*\{.*\}\s*\]', text, re.DOTALL)
        if json_array_match:
            try:
                parsed = json.loads(json_array_match.group(0))
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict):
                            normalized = self._normalize_finding(item, requirement_counter)
                            if normalized:
                                findings.append(normalized)
                                requirement_counter += 1
            except json.JSONDecodeError:
                pass

        # Also look for individual JSON objects with proper structure
        # Match { "key": "value", ... } patterns that have multiple fields
        json_object_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        for json_str in re.findall(json_object_pattern, text):
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, dict) and len(parsed) >= 3:
                    normalized = self._normalize_finding(parsed, requirement_counter)
                    if normalized:
                        findings.append(normalized)
                        requirement_counter += 1
            except json.JSONDecodeError:
                continue

        # Pattern 4: Look for severity patterns like "严重程度: critical" or "severity: major"
        severity_patterns = re.findall(
            r'(?:严重程度|severity|级别)\s*[:：]\s*(critical|major|minor|严重|一般|轻微)',
            text,
            re.IGNORECASE
        )

        # Pattern 5: Extract requirement-bid pairs
        requirement_bid_pattern = re.findall(
            r'(?:要求|需求|requirement)[:：]?\s*["\']?([^"\'\n]+)["\']?[^"]*(?:投标|bid|应标)[:：]?\s*["\']?([^"\'\n]+)["\']?',
            text,
            re.IGNORECASE | re.DOTALL
        )

        for req, bid in requirement_bid_pattern:
            findings.append({
                "requirement_key": f"req_{requirement_counter:03d}",
                "requirement_content": req.strip(),
                "bid_content": bid.strip() if bid else None,
                "is_compliant": False,
                "severity": "minor",
                "location_page": None,
                "location_line": None,
                "suggestion": None,
                "explanation": f"发现不符合项：要求 {req.strip()}",
            })
            requirement_counter += 1

        # Pattern 6: Look for "is_compliant" or "compliant" patterns
        compliant_pattern = re.findall(
            r'(?:is_compliant|compliant|合规状态)\s*[:＝=]\s*(true|false|是|否|符合|不合规)',
            text,
            re.IGNORECASE
        )

        # Pattern 7: Try to extract lines that look like finding descriptions
        # Look for lines containing both requirement-like and compliance-like content
        for line in text.split('\n'):
            line = line.strip()
            if not line or len(line) < 10:
                continue

            # Check if line contains a requirement indicator and compliance indicator
            has_requirement = any(kw in line.lower() for kw in ['要求', '需求', '规格', '标准', '规定', '需要', '必须'])
            has_compliance = any(kw in line.lower() for kw in ['符合', '不合规', '不满足', '缺失', '违规', '缺少', '未提供', '通过'])

            if has_requirement or len(compliance_lines) > 0:
                # This looks like a finding line
                is_compliant = not any(kw in line.lower() for kw in ['不合规', '不满足', '缺失', '违规', '缺少', '未提供'])
                severity = self._infer_severity(line)

                findings.append({
                    "requirement_key": f"req_{requirement_counter:03d}",
                    "requirement_content": line[:200] if len(line) > 200 else line,
                    "bid_content": None,
                    "is_compliant": is_compliant,
                    "severity": severity if not is_compliant else None,
                    "location_page": None,
                    "location_line": None,
                    "suggestion": None,
                    "explanation": line,
                })
                requirement_counter += 1

        # Deduplicate findings by content
        seen_content = set()
        unique_findings = []
        for f in findings:
            content_key = f.get("requirement_content", "")[:50]
            if content_key not in seen_content:
                seen_content.add(content_key)
                unique_findings.append(f)

        # If we found structured findings, return them
        if unique_findings:
            return unique_findings[:20]  # Limit to 20 findings

        # Fallback: return full text as summary
        return [{
            "requirement_key": "review_summary",
            "requirement_content": "投标文件审查结果汇总",
            "bid_content": None,
            "is_compliant": True,
            "severity": "minor",
            "location_page": None,
            "location_line": None,
            "suggestion": None,
            "explanation": text[:2000] if len(text) > 2000 else text,
        }]

    def _infer_severity(self, text: str) -> Optional[str]:
        """Infer severity from text content.

        Args:
            text: Text to analyze

        Returns:
            Severity level string or None.
        """
        text_lower = text.lower()

        # Critical indicators
        critical_keywords = ['严重', '关键', '致命', '重大', 'critical', 'fatal', 'major']
        if any(kw in text_lower for kw in critical_keywords):
            return "critical"

        # Major indicators
        major_keywords = ['重要', '较大', '主要', 'major', 'important', 'significant']
        if any(kw in text_lower for kw in major_keywords):
            return "major"

        # Minor indicators
        minor_keywords = ['轻微', '一般', '较小', '次要', 'minor', 'small', 'slight']
        if any(kw in text_lower for kw in minor_keywords):
            return "minor"

        return "minor"  # Default to minor

    async def decide_merge(
        self,
        new_finding: dict,
        existing_findings: list[dict],
    ) -> str:
        """调用 LLM 进行合并决策。

        Args:
            new_finding: 新发现的完整信息
            existing_findings: 现有发现列表

        Returns:
            LLM 自然语言决策结果
        """
        tool = MergeDeciderTool()
        result = await tool.execute(new_finding, existing_findings)

        if not result.success:
            raise RuntimeError(f"Merge decider failed: {result.error}")

        return result.content

    async def close(self):
        """Close MCP connections and cleanup resources."""
        await cleanup_mcp_connections()

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
        import logging
        logger = logging.getLogger(__name__)
        last_exception = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[BidReviewAgent] LLM attempt {attempt}/{max_retries}")
                response = await self.llm_client.generate(messages=messages)
                logger.info(f"[BidReviewAgent] LLM attempt {attempt} succeeded")
                return response
            except Exception as e:
                last_exception = e
                logger.warning(f"[BidReviewAgent] LLM attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1)

        # All retries failed
        logger.error(f"[BidReviewAgent] All {max_retries} LLM attempts failed")
        raise last_exception
