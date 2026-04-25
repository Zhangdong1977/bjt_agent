"""Bid Review Agent - extends Mini-Agent with custom tools.

This agent is responsible for comparing tender documents against bid documents
and identifying non-compliant items.
"""

import asyncio
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

# Ensure Mini-Agent path is in sys.path before importing mini_agent modules
from backend.utils.mini_agent_utils import setup_mini_agent_path
setup_mini_agent_path()

from mini_agent.agent import Agent as BaseAgent
from mini_agent.llm import LLMClient
from mini_agent.schema import LLMProvider, Message
from mini_agent.tools.mcp_loader import load_mcp_tools_async, cleanup_mcp_connections
from mini_agent.tools.file_tools import WriteTool, ReadTool

from backend.config import get_settings
from backend.agent.tools.doc_search import DocSearchTool
from backend.agent.tools.rag_search import RAGSearchTool
from backend.agent.tools.comparator import ComparatorTool
from backend.agent.tools import MergeDeciderTool
from backend.agent.prompt import SYSTEM_PROMPT_WITH_RULE

settings = get_settings()


class BidReviewAgent(BaseAgent):
    """Bid review agent that extends Mini-Agent with domain-specific tools."""

    def __init__(
        self,
        project_id: str,
        tender_doc_path: str,
        bid_doc_path: str,
        user_id: str,
        rule_doc_path: str,
        event_callback=None,
        logger=None,
        max_steps: int = 100,
        cancel_event: Optional[asyncio.Event] = None,
        heartbeat_timeout: int = 20,
    ):
        """Initialize the bid review agent (synchronous part).

        Args:
            project_id: The project ID for organizing workspace
            tender_doc_path: Path to the parsed tender document
            bid_doc_path: Path to the parsed bid document
            user_id: The user ID for workspace organization
            rule_doc_path: Path to the rule document for this review
            event_callback: Optional callback for SSE event publishing
            logger: Optional logger for file output. If None, uses module logger.
            max_steps: Maximum number of agent steps
            cancel_event: Optional asyncio.Event to signal cancellation
            heartbeat_timeout: Heartbeat timeout in seconds (default 20)
        """
        self.project_id = project_id
        self.tender_doc_path = tender_doc_path
        self.bid_doc_path = bid_doc_path
        self.user_id = user_id
        self.rule_doc_path = rule_doc_path
        self.event_callback = event_callback
        self._logger = logger
        self.cancel_event = cancel_event
        self.heartbeat_timeout = heartbeat_timeout
        self._task_id: Optional[str] = None
        self._findings: list[dict] = []
        # Store tool results for persistence via _record_agent_step
        # Use list to preserve order and allow multiple calls to same tool
        self._tool_results: list[dict] = []
        # Track the starting index for each step's tool results
        self._tool_results_step_start: int = 0
        # Track accumulated step data for consolidated sub_agent_step events
        self._step_data: dict[int, dict] = {}

        # Initialize LLM client (MiniMax uses OpenAI protocol)
        llm_client = LLMClient(
            api_key=settings.mini_agent_api_key,
            provider=LLMProvider.OPENAI,  # MiniMax uses OpenAI-compatible API
            api_base=settings.mini_agent_api_base,
            model=settings.mini_agent_model,
        )
        self.llm_client = llm_client  # Store for _call_llm_with_retry

        # Set up workspace
        workspace_dir = settings.workspace_path / str(user_id) / str(project_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Initialize tools
        tools = [
            DocSearchTool(tender_doc_path=tender_doc_path, bid_doc_path=bid_doc_path),
            RAGSearchTool(user_id=user_id),
            ComparatorTool(),
            MergeDeciderTool(),
            WriteTool(workspace_dir=str(workspace_dir)),
            ReadTool(workspace_dir=str(workspace_dir)),
        ]

        # Initialize base agent with event_callback
        super().__init__(
            llm_client=llm_client,
            system_prompt=SYSTEM_PROMPT_WITH_RULE,
            tools=tools,
            workspace_dir=str(workspace_dir),
            max_steps=max_steps,
            event_callback=event_callback,
        )

    async def initialize(self):
        """Async initialization - load MCP tools (call after construction)."""
        import logging
        import sys as _sys
        import os as _os
        import subprocess as _subprocess
        logger = self._logger or logging.getLogger(__name__)

        # Ensure MINIMAX_API_KEY and MINIMAX_API_HOST are set in os.environ
        # before loading MCP tools. This is required because:
        # 1. Celery workers don't inherit .env file automatically
        # 2. mcp_loader's env expansion uses os.environ.get() which returns None
        # 3. The subprocess would get literal "${MINIMAX_API_KEY}" instead of actual key
        if not _os.environ.get("MINIMAX_API_KEY") and settings.minimax_api_key:
            _os.environ["MINIMAX_API_KEY"] = settings.minimax_api_key
            logger.info("[BidReviewAgent.initialize] Set MINIMAX_API_KEY from settings")
        if not _os.environ.get("MINIMAX_API_HOST") and settings.minimax_api_host:
            _os.environ["MINIMAX_API_HOST"] = settings.minimax_api_host
            logger.info("[BidReviewAgent.initialize] Set MINIMAX_API_HOST from settings")

        # Fix for Celery's LoggingProxy which breaks asyncio subprocess creation.
        # Celery replaces sys.stderr with a LoggingProxy that has no fileno().
        # The mcp library's stdio_client calls anyio.open_process with stderr=errlog,
        # which eventually calls subprocess.Popen. In Popen._get_handles(),
        # stderr.fileno() is called TWICE:
        #   1. First call: to get the fd number and check if it's a tty (via isatty())
        #   2. Second call: to actually dup2 the fd
        # The problem: LoggingProxy.fileno() raises AttributeError.
        # Our previous StderrWrapper approach failed because each fileno() call
        # opened a NEW /dev/null fd, causing the fd to change between the two calls,
        # making os.fstat() fail and Popen falling back to the original stderr.
        # Solution: keep a SINGLE /dev/null fd open for the entire _connect_stdio call,
        # and pass it to the subprocess via monkeypatching StdioServerParameters.

        # Check if we have LoggingProxy
        _orig_stderr = _sys.stderr
        _needs_fix = False
        try:
            _sys.stderr.fileno()
        except AttributeError:
            _needs_fix = True

        _devnull_fd = None
        _old_stderr = None

        if _needs_fix:
            # Open /dev/null ONCE and keep it open for the duration
            _devnull_fd = _os.open(_os.devnull, _os.O_WRONLY)

            class _StderrWrapper:
                """Wrapper that returns a FIXED /dev/null fd for every fileno() call."""
                def __init__(self, orig, fd):
                    self._orig = orig
                    self._fd = fd
                def write(self, s):
                    return self._orig.write(s)
                def flush(self):
                    return self._orig.flush()
                def fileno(self):
                    return self._fd  # Same fd every time!
                def isatty(self):
                    return False

            _old_stderr = _StderrWrapper(_orig_stderr, _devnull_fd)
            _sys.stderr = _old_stderr
            logger.info(f"[BidReviewAgent.initialize] Wrapped LoggingProxy.stderr with fixed fd={_devnull_fd}")

            # The problem: stdio_client's default errlog=sys.stderr was captured at
            # IMPORT TIME (when the module was first loaded, stderr was LoggingProxy).
            # So even though we replaced sys.stderr, stdio_client still uses the
            # old LoggingProxy via its default argument.
            # Fix: patch _create_platform_compatible_process to use DEVNULL for stderr.
            # This is the function that actually passes stderr to subprocess.Popen.
            try:
                import anyio
                _original_open_process = anyio.open_process
                async def _patched_open_process(*args, **kwargs):
                    kwargs['stderr'] = _subprocess.DEVNULL
                    return await _original_open_process(*args, **kwargs)
                anyio.open_process = _patched_open_process
                logger.info("[BidReviewAgent.initialize] Patched anyio.open_process to use stderr=DEVNULL")
            except Exception as e:
                logger.warning(f"[BidReviewAgent.initialize] Could not patch anyio.open_process: {e}")

        try:
            # Load MCP tools (MiniMax-Coding-Plan-MCP)
            mcp_config_path = Path(__file__).parent.parent / "mcp.json"
            if mcp_config_path.exists():
                from mini_agent.tools.mcp_loader import set_mcp_timeout_config
                set_mcp_timeout_config(connect_timeout=60.0, execute_timeout=120.0)

                mcp_tools = await load_mcp_tools_async(str(mcp_config_path))
                loaded_tool_names = []
                for tool in mcp_tools:
                    self.tools[tool.name] = tool
                    loaded_tool_names.append(tool.name)

                logger.info(f"[BidReviewAgent.initialize] Successfully loaded {len(mcp_tools)} MCP tools: {loaded_tool_names}")

                if "understand_image" in loaded_tool_names:
                    logger.info("[BidReviewAgent.initialize] Image understanding tool (understand_image) is available")
                if "web_search" in loaded_tool_names:
                    logger.info("[BidReviewAgent.initialize] Web search tool (web_search) is available")
            else:
                logger.warning(f"[BidReviewAgent.initialize] MCP config not found at {mcp_config_path}")

        except Exception as e:
            logger.warning(f"[BidReviewAgent.initialize] Failed to load MCP tools: {e}")
        finally:
            if _needs_fix:
                _sys.stderr = _orig_stderr
                if _devnull_fd is not None:
                    _os.close(_devnull_fd)
                try:
                    anyio.open_process = _original_open_process
                    logger.info("[BidReviewAgent.initialize] Restored anyio.open_process")
                except Exception:
                    pass
                logger.info("[BidReviewAgent.initialize] Restored original LoggingProxy.stderr")

    def _send_event(self, event_type: str, data: dict) -> None:
        """Send an event via callback if available."""
        import logging
        logger = self._logger or logging.getLogger(__name__)
        logger.info(f"[BidReviewAgent._send_event] type={event_type}, data_keys={list(data.keys())}, callback_exists={self.event_callback is not None}")
        if self.event_callback:
            try:
                self.event_callback(event_type, data)
                logger.info(f"[BidReviewAgent._send_event] Successfully sent event type={event_type}")
            except Exception as e:
                logger.error(f"[BidReviewAgent._send_event] Failed to send event: {e}")

    def _check_heartbeat(self) -> bool:
        """Check if heartbeat has exceeded timeout.

        Returns:
            True if heartbeat is OK (within timeout or no task context),
            False if exceeded timeout.
        """
        import logging
        logger = self._logger or logging.getLogger(__name__)

        # No task context means we're in standalone mode without heartbeat tracking
        if not hasattr(self, '_task_id') or not self._task_id:
            return True

        from sqlalchemy import select
        from backend.models import ReviewTask
        from backend.models.base import async_session_factory

        try:
            async def _check():
                async with async_session_factory() as db:
                    result = await db.execute(
                        select(ReviewTask).where(ReviewTask.id == self._task_id)
                    )
                    task = result.scalar_one_or_none()
                    if not task or not task.last_heartbeat:
                        return True  # No heartbeat yet, assume OK

                    elapsed = (datetime.utcnow() - task.last_heartbeat).total_seconds()
                    if elapsed > self.heartbeat_timeout:
                        logger.warning(
                            f"[BidReviewAgent] Heartbeat timeout: {elapsed:.1f}s > {self.heartbeat_timeout}s"
                        )
                        return False
                    return True

            import asyncio
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(_check())
        except Exception as e:
            logger = self._logger or logging.getLogger(__name__)
            logger.warning(f"[BidReviewAgent] Heartbeat check failed: {e}")
            return True  # Fail open

    async def _heartbeat_monitor_loop(self) -> None:
        """Background loop that monitors heartbeat and sets cancel_event on timeout."""
        import logging
        logger = self._logger or logging.getLogger(__name__)

        while not self.cancel_event.is_set():
            await asyncio.sleep(5)  # Check every 5 seconds

            if self.cancel_event.is_set():
                break

            if not self._check_heartbeat():
                logger.warning("[BidReviewAgent] Setting cancel_event due to heartbeat timeout")
                self.cancel_event.set()
                break

    def _emit_event(self, event_type: str, data: dict) -> None:
        """Override _emit_event to consolidate granular events into sub_agent_step.

        The base Mini-Agent emits: step_start, llm_output, tool_call_start,
        tool_call_end, step_complete. The frontend expects consolidated
        sub_agent_step events with step_number, step_type, content, tool_calls,
        and tool_results.

        This override intercepts the granular events, accumulates them per step,
        and emits a consolidated sub_agent_step event when step_complete is received.
        """
        import logging
        logger = self._logger or logging.getLogger(__name__)

        if event_type == "step_start":
            # Initialize step data accumulation
            step = data.get("step", 0)
            self._step_data[step] = {
                "step_number": step,
                "step_type": "unknown",
                "content": "",
                "tool_calls": [],
                "tool_results": [],
                "timestamp": datetime.now(),
            }
            logger.info(f"[BidReviewAgent._emit_event] step_start for step {step}")

        elif event_type == "llm_output":
            # Accumulate LLM output as content/thinking
            step = data.get("step", 0)
            if step in self._step_data:
                content = data.get("content", "") or ""
                thinking = data.get("thinking") or ""
                # Determine step_type based on content presence
                if content and data.get("tool_calls"):
                    step_type = "observation"
                elif content:
                    step_type = "thought"
                elif data.get("tool_calls"):
                    step_type = "tool_call"
                else:
                    step_type = "unknown"
                self._step_data[step].update({
                    "step_type": step_type,
                    "content": content,
                    "thinking": thinking,
                    "tool_calls": data.get("tool_calls", []),
                })
                logger.info(f"[BidReviewAgent._emit_event] llm_output for step {step}, tool_calls_count={len(data.get('tool_calls', []))}")

        elif event_type == "tool_call_start":
            # Tool call started - tool_calls already accumulated from llm_output
            step = data.get("step", 0)
            if step in self._step_data:
                logger.info(f"[BidReviewAgent._emit_event] tool_call_start for step {step}, tool={data.get('tool')}")

        elif event_type == "tool_call_end":
            # Accumulate tool result
            step = data.get("step", 0)
            if step in self._step_data:
                tool_result = {
                    "id": data.get("tool_call_id", ""),
                    "name": data.get("tool", ""),
                    "status": "success" if data.get("success") else "error",
                    "content": data.get("result") if data.get("success") else None,
                    "error": data.get("error") if not data.get("success") else None,
                }
                self._step_data[step]["tool_results"].append(tool_result)
                result_preview = str(data.get("result"))[:200] if data.get("result") else "None"
                logger.info(f"[BidReviewAgent._emit_event] tool_call_end for step {step}, tool={data.get('tool')}, success={data.get('success')}, result={result_preview}")

        elif event_type == "step_complete":
            # Emit consolidated sub_agent_step event
            step = data.get("step", 0)
            if step in self._step_data:
                step_info = self._step_data[step]
                # Convert tool_results to frontend format
                frontend_tool_results = []
                for tr in step_info["tool_results"]:
                    frontend_tool_results.append({
                        "name": tr["name"],
                        "result": {
                            "status": tr["status"],
                            "content": tr["content"],
                            "error": tr.get("error"),
                        }
                    })

                consolidated_event = {
                    "step_number": step_info["step_number"],
                    "step_type": step_info["step_type"],
                    "content": step_info["content"],
                    "timestamp": step_info["timestamp"].isoformat(),
                    "tool_calls": step_info["tool_calls"],
                    "tool_results": frontend_tool_results,
                }
                # Debug: log what we're actually sending
                for i, tr in enumerate(frontend_tool_results):
                    logger.info(f"[BidReviewAgent._emit_event] step_complete step={step}, tool_result[{i}]: name={tr['name']}, result_keys={list(tr['result'].keys())}, content_len={len(str(tr['result'].get('content', '')))}")

                # Send consolidated event via callback
                if self.event_callback:
                    try:
                        self.event_callback("sub_agent_step", consolidated_event)
                        logger.info(f"[BidReviewAgent._emit_event] Emitted sub_agent_step for step {step}")
                    except Exception as e:
                        logger.error(f"[BidReviewAgent._emit_event] Failed to emit sub_agent_step: {e}")

                # Clean up
                del self._step_data[step]
            logger.info(f"[BidReviewAgent._emit_event] step_complete for step {step}")

        elif event_type == "completed":
            # Agent completed - forward to callback
            if self.event_callback:
                try:
                    self.event_callback("completed", data)
                    logger.info(f"[BidReviewAgent._emit_event] Forwarded completed event")
                except Exception as e:
                    logger.error(f"[BidReviewAgent._emit_event] Failed to emit completed: {e}")

    def _load_rule_doc(self) -> str:
        """Read the rule document full text for building system prompt.

        Returns:
            The full text content of the rule document.
        Raises:
            FileNotFoundError: If rule doc path does not exist.
        """
        from pathlib import Path
        path = Path(self.rule_doc_path)
        if not path.exists():
            raise FileNotFoundError(f"Rule doc not found: {self.rule_doc_path}")
        return path.read_text(encoding="utf-8")

    def _build_system_prompt(self, rule_doc_content: str) -> str:
        """Build system prompt containing rule document content.

        Args:
            rule_doc_content: Full text content of the rule document.

        Returns:
            System prompt string with rule content embedded.
        """
        from backend.agent.prompt import SYSTEM_PROMPT_WITH_RULE
        tender_doc_directory = str(Path(self.tender_doc_path).parent)
        bid_doc_directory = str(Path(self.bid_doc_path).parent)
        return SYSTEM_PROMPT_WITH_RULE.format(
            rule_doc_content=rule_doc_content,
            tender_doc_directory=tender_doc_directory,
            bid_doc_directory=bid_doc_directory,
        )

    async def run_review(self) -> list[dict]:
        """Run the bid review process using rule file.

        This method:
        1. Loads the rule document
        2. Builds system prompt with rule content
        3. Calls base class run() (reuses Mini-Agent loop with event_callback)
        4. Post-processes to extract findings from md output file

        Returns:
            List of findings with requirement, bid content, compliance status, etc.
        """
        import logging
        import time
        logger = self._logger or logging.getLogger(__name__)
        logger.info(f"[BidReviewAgent.run_review] Starting, tender={self.tender_doc_path}, bid={self.bid_doc_path}, rule_doc={self.rule_doc_path}")

        try:
            # 1. Read rule file
            rule_doc_content = self._load_rule_doc()
            logger.info(f"[BidReviewAgent.run_review] Rule doc loaded, size={len(rule_doc_content)} chars")

            # 2. Build system prompt
            system_prompt = self._build_system_prompt(rule_doc_content)
            self.system_prompt = system_prompt
            # Update system message in messages list
            self.messages[0] = Message(role="system", content=system_prompt)
            logger.info(f"[BidReviewAgent.run_review] System prompt built, size={len(system_prompt)} chars")

            # Log full system prompt
            logger.info(f"[BidReviewAgent.run_review] === SYSTEM PROMPT START ===")
            logger.info(f"\n{system_prompt}")
            logger.info(f"[BidReviewAgent.run_review] === SYSTEM PROMPT END ===")

            # 3. Build task prompt with output md path
            output_md_path = str(self.workspace_dir / f"review_{int(time.time())}.md")
            task = f"""请执行投标文件审查任务：
- 招标书路径: {self.tender_doc_path}
- 投标书路径: {self.bid_doc_path}
- 审查结果输出文件: {output_md_path}

请按照系统提示词中的规则执行审查，并将结果直接写入到上述 md 文件中。
重要：必须使用 WriteTool 将审查结果写入文件。"""
            self.add_user_message(task)
            logger.info(f"[BidReviewAgent.run_review] Task prompt added, output_md_path={output_md_path}")

            # Log full task prompt
            logger.info(f"[BidReviewAgent.run_review] === TASK PROMPT START ===")
            logger.info(f"\n{task}")
            logger.info(f"[BidReviewAgent.run_review] === TASK PROMPT END ===")

            # Create cancel event if not provided
            if self.cancel_event is None:
                self.cancel_event = asyncio.Event()

            # Start heartbeat monitor as background task
            heartbeat_monitor = asyncio.create_task(
                self._heartbeat_monitor_loop()
            )

            try:
                # 4. Call base class run() - reuses Mini-Agent loop with event_callback
                await self.run(cancel_event=self.cancel_event)
                logger.info(f"[BidReviewAgent.run_review] Agent.run() completed")
            finally:
                heartbeat_monitor.cancel()
                try:
                    await heartbeat_monitor
                except asyncio.CancelledError:
                    pass

            # Log full message history
            logger.info(f"[BidReviewAgent.run_review] === FULL MESSAGE HISTORY START ===")
            for i, msg in enumerate(self.messages):
                msg_header = f"[Message {i}] role={msg.role}"
                if msg.thinking:
                    msg_header += f", thinking_length={len(msg.thinking)}"
                if msg.tool_calls:
                    msg_header += f", tool_calls={[tc.function.name for tc in msg.tool_calls]}"
                if msg.tool_call_id:
                    msg_header += f", tool_call_id={msg.tool_call_id}"
                logger.info(f"[BidReviewAgent.run_review] {msg_header}")
                if msg.content:
                    # Truncate very long content for logging
                    content_preview = msg.content[:5000] + "..." if len(msg.content) > 5000 else msg.content
                    logger.info(f"[BidReviewAgent.run_review] [Message {i}] content:\n{content_preview}")
                if msg.thinking:
                    thinking_preview = msg.thinking[:2000] + "..." if len(msg.thinking) > 2000 else msg.thinking
                    logger.info(f"[BidReviewAgent.run_review] [Message {i}] thinking:\n{thinking_preview}")
            logger.info(f"[BidReviewAgent.run_review] === FULL MESSAGE HISTORY END ===")

            # 5. Post-process: extract findings from md file
            findings = await self._post_process(output_md_path)
            logger.info(f"[BidReviewAgent.run_review] Post-processing completed, found {len(findings)} findings")

            return findings

        except Exception as e:
            logger.exception(f"[BidReviewAgent.run_review] Exception: {e}")
            return []

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
        critical_keywords = ['严重', '关键', '致命', '重大', 'critical', 'fatal']
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
        logger = self._logger or logging.getLogger(__name__)
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

    async def _post_process(self, md_path: str) -> list[dict]:
        """Post-process md file to extract structured findings.

        Args:
            md_path: Path to the markdown output file.

        Returns:
            List of structured findings.
        """
        import logging
        from pathlib import Path
        logger = self._logger or logging.getLogger(__name__)

        # Try to read the md file
        md_file = Path(md_path)
        if not md_file.exists():
            logger.warning(f"[BidReviewAgent._post_process] MD file not found: {md_path}")
            return []

        md_content = md_file.read_text(encoding="utf-8")
        logger.info(f"[BidReviewAgent._post_process] MD file read, size={len(md_content)} chars")

        # Try rule-based parsing first
        findings = self._parse_md_findings(md_content)
        if findings:
            logger.info(f"[BidReviewAgent._post_process] Rule-based parsing succeeded, found {len(findings)} findings")
            return findings

        # Fallback to LLM extraction
        logger.info(f"[BidReviewAgent._post_process] Rule-based parsing failed, falling back to LLM extraction")
        findings = await self._llm_extract_findings(md_content)
        logger.info(f"[BidReviewAgent._post_process] LLM extraction completed, found {len(findings)} findings")
        return findings

    def _parse_md_findings(self, md_content: str) -> Optional[list[dict]]:
        """Parse findings from md content using rule-based approach.

        Args:
            md_content: Markdown content string.

        Returns:
            List of findings if parsing succeeds, None otherwise.
        """
        import logging
        logger = self._logger or logging.getLogger(__name__)

        findings = []
        requirement_counter = 1

        # Pattern to match finding sections
        # Looking for "## 检查项{N}: {name}" headers
        import re
        section_pattern = r'##\s*检查项\d+:\s*(.+?)(?=\n##|\Z)'
        sections = re.split(r'(?=##\s*检查项)', md_content)

        for section in sections:
            if not section.strip():
                continue

            # Extract section name
            header_match = re.match(r'##\s*检查项\d+:\s*(.+)', section)
            if not header_match:
                continue
            check_item_name = header_match.group(1).strip()

            # Extract fields from section
            rule_desc = self._extract_field(section, '规则项')
            tender_req = self._extract_field(section, '招标书要求')
            bid_content = self._extract_field(section, '应标书内容')
            explanation = self._extract_field(section, '不符合项说明')
            severity = self._extract_severity_from_section(section)

            if tender_req:
                finding = {
                    "requirement_key": f"req_{requirement_counter:03d}",
                    "requirement_content": tender_req,
                    "bid_content": bid_content,
                    "is_compliant": severity is None,  # If no severity, it's compliant
                    "severity": severity,
                    "location_page": None,
                    "location_line": None,
                    "suggestion": None,
                    "explanation": explanation or "",
                }
                findings.append(finding)
                requirement_counter += 1

        if not findings:
            return None

        return findings

    def _extract_field(self, section: str, field_name: str) -> Optional[str]:
        """Extract a field value from a section.

        Args:
            section: Section content.
            field_name: Field name to extract.

        Returns:
            Field value or None if not found.
        """
        import re
        pattern = rf'###\s*{re.escape(field_name)}\s*\n(.+?)(?=\n###|\Z)'
        match = re.search(pattern, section, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    def _extract_severity_from_section(self, section: str) -> Optional[str]:
        """Extract severity from section.

        Args:
            section: Section content.

        Returns:
            Severity string or None if not found/compliant.
        """
        import re
        pattern = r'###\s*严重程度\s*\n(.+?)(?=\n###|\Z)'
        match = re.search(pattern, section, re.DOTALL)
        if match:
            severity_text = match.group(1).strip().lower()
            if 'critical' in severity_text:
                return 'critical'
            elif 'major' in severity_text:
                return 'major'
            elif 'minor' in severity_text:
                return 'minor'
        return None

    async def _llm_extract_findings(self, md_content: str) -> list[dict]:
        """Extract findings using LLM.

        Args:
            md_content: Markdown content string.

        Returns:
            List of structured findings.
        """
        import logging
        logger = self._logger or logging.getLogger(__name__)

        extract_prompt = f"""你是一个结构化数据提取专家。请从以下投标文件审查结果的 Markdown 文档中提取所有不符合项，输出为 JSON 数组。

## 输出格式
[
  {{
    "requirement_key": "检查项编号",
    "requirement_content": "招标书要求",
    "bid_content": "应标书内容",
    "is_compliant": false,
    "severity": "critical/major/minor",
    "explanation": "不符合项说明"
  }},
  ...
]

## Markdown 内容
{md_content}

## 要求
1. 只提取不符合项（is_compliant=false 的项）
2. severity 必须是 "critical"、"major" 或 "minor" 之一
3. 如果所有检查都符合要求，返回空数组 []
4. 输出必须是有效的 JSON 数组"""

        try:
            response = await self.llm_client.generate(
                messages=[
                    Message(role="system", content="你是一个结构化数据提取专家。"),
                    Message(role="user", content=extract_prompt),
                ],
                tools=[],
            )

            if not response.content:
                return []

            # Try to parse JSON from response
            parsed = self._try_parse_json(response.content)
            if parsed and isinstance(parsed, list):
                findings = []
                for i, item in enumerate(parsed):
                    if isinstance(item, dict):
                        normalized = self._normalize_finding(item, i + 1)
                        if normalized:
                            findings.append(normalized)
                return findings

        except Exception as e:
            logger.exception(f"[BidReviewAgent._llm_extract_findings] LLM extraction failed: {e}")

        return []
