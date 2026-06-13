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
from mini_agent.schema import Message

from backend.services.llm_factory import create_llm_client
from mini_agent.tools.mcp_loader import load_mcp_tools_async, cleanup_mcp_connections
from mini_agent.tools.file_tools import WriteTool, ReadTool

from backend.config import get_settings
from backend.agent.tools.doc_search import DocSearchTool
from backend.agent.tools.rag_search import RAGSearchTool
from backend.agent.tools.comparator import ComparatorTool
from backend.agent.tools.structure_tools import (
    DocumentTocTool,
    SectionContentTool,
    SectionImagesTool,
    ImageOcrTool,
    StructureDataLoader,
)
from backend.agent.tools import MergeDeciderTool
from backend.agent.prompt import SYSTEM_PROMPT_WITH_RULE

settings = get_settings()

# Module-level LLM concurrency semaphore: shared across all BidReviewAgent instances
# within the same process (Celery worker). Limits concurrent LLM API calls to
# avoid overwhelming the MiniMax API rate limit.
# Uses a separate config (max_llm_concurrency) from sub-agent concurrency.
# The semaphore is re-created if the event loop changes (e.g., across asyncio.run() calls).
_llm_semaphore: asyncio.Semaphore | None = None
_llm_semaphore_loop_id: int | None = None


def _get_llm_semaphore() -> asyncio.Semaphore:
    """Get or create the shared LLM concurrency semaphore.

    Re-creates the semaphore if the event loop has changed (happens when
    Celery workers call asyncio.run() for each task).
    """
    global _llm_semaphore, _llm_semaphore_loop_id
    current_loop = asyncio.get_running_loop()
    current_loop_id = id(current_loop)
    if _llm_semaphore is None or _llm_semaphore_loop_id != current_loop_id:
        max_conc = settings.max_llm_concurrency or settings.max_sub_agent_concurrency
        _llm_semaphore = asyncio.Semaphore(max_conc)
        _llm_semaphore_loop_id = current_loop_id
    return _llm_semaphore


class BidReviewAgent(BaseAgent):
    """Bid review agent that extends Mini-Agent with domain-specific tools."""

    def __init__(
        self,
        project_id: str,
        tender_docs: list[tuple[str, str]],
        bid_docs: list[tuple[str, str]],
        user_id: str,
        rule_doc_path: str,
        event_callback=None,
        logger=None,
        max_steps: int = 100,
        cancel_event: Optional[asyncio.Event] = None,
        heartbeat_timeout: int = 60,
        heartbeat_session_factory=None,
    ):
        """Initialize the bid review agent (synchronous part).

        初始化投标评审代理的同步部分，配置项目参数、工作空间、工具集及LLM客户端。

        Args:
            project_id: The project ID for organizing workspace
            tender_docs: List of (filename, parsed_md_path) for tender documents
            bid_docs: List of (filename, parsed_md_path) for bid documents
            user_id: The user ID for workspace organization
            rule_doc_path: Path to the rule document for this review
            event_callback: Optional callback for SSE event publishing
            logger: Optional logger for file output. If None, uses module logger.
            max_steps: Maximum number of agent steps
            cancel_event: Optional asyncio.Event to signal cancellation
            heartbeat_timeout: Heartbeat timeout in seconds (default 60)
        """
        self.project_id = project_id
        self.tender_docs = tender_docs
        self.bid_docs = bid_docs
        self.user_id = user_id
        self.rule_doc_path = rule_doc_path
        self.event_callback = event_callback
        self._logger = logger
        self.heartbeat_timeout = heartbeat_timeout
        # Heartbeat DB session: prefer the per-task factory injected by SubAgentExecutor
        # (lifecycle-matched to the running task) over the module-level engine, which can
        # suffer connection-pool staleness in long-running Celery workers.
        self._heartbeat_session_factory = heartbeat_session_factory
        # Fail-closed counter: tolerate transient DB hiccups (default 3 x 5s poll ≈ 15s)
        # before declaring the task dead. Resets to 0 on every successful check.
        self._heartbeat_fail_count: int = 0
        self._heartbeat_fail_threshold: int = settings.heartbeat_fail_threshold
        self._task_id: Optional[str] = None
        self._findings: list[dict] = []
        self._owns_mcp_cleanup: bool = True  # Sub-agents set this to False
        # Store tool results for persistence via _record_agent_step
        # Use list to preserve order and allow multiple calls to same tool
        self._tool_results: list[dict] = []
        # Track the starting index for each step's tool results
        self._tool_results_step_start: int = 0
        # Track accumulated step data for consolidated sub_agent_step events
        self._step_data: dict[int, dict] = {}
        # Track total completed steps (never decremented, used for brain_capacity)
        self._total_steps: int = 0
        # Track the reason for cancellation (heartbeat_timeout vs api_cancellation)
        self._cancel_reason: Optional[str] = None
        # Track whether max_steps was reached during execution
        self._max_steps_exceeded: bool = False

        # Duplicate action detection
        from collections import deque
        self._tool_call_history: deque = deque(maxlen=20)  # Recent tool call signatures
        self._duplicate_warning_count: int = 0  # Warnings issued so far
        self._write_file_called: bool = False  # Whether write_file has been invoked

        # Initialize LLM client via factory (supports MiniMax / Volcengine)
        llm_client = create_llm_client(timeout=120.0)
        self.llm_client = llm_client  # Store for _call_llm_with_retry

        # Set up workspace
        workspace_dir = settings.workspace_path / str(user_id) / str(project_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Initialize tools
        # Shared loaders for structured tools (avoids duplicate file reads)
        from backend.agent.tools.structure_tools import _create_shared_loaders
        _shared_loaders = _create_shared_loaders(tender_docs, bid_docs)

        tools = [
            DocSearchTool(tender_docs=tender_docs, bid_docs=bid_docs),
            RAGSearchTool(user_id=user_id),
            ComparatorTool(),
            MergeDeciderTool(),
            DocumentTocTool(loaders=_shared_loaders),
            SectionContentTool(loaders=_shared_loaders),
            SectionImagesTool(loaders=_shared_loaders),
            ImageOcrTool(loaders=_shared_loaders, ocr_service_url=settings.ocr_service_url),
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
            token_limit=settings.agent_token_limit,
        )

        # Set cancel_event AFTER super().__init__() to avoid being overwritten
        self.cancel_event = cancel_event

        # LLM interaction logging
        self._interactions_log: list[dict] = []
        self._llm_call_count: int = 0
        self._original_llm_generate = None
        self._wrap_llm_generate()

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

                # Override understand_image with the configured image-understanding provider.
                # - image_understanding_provider == "baidu": 百度云 OCR（纯文字识别）
                # - "volcengine"（或旧 LLM_PROVIDER=volcengine）：火山豆包视觉（VLM）
                # - 其他（默认 minimax）：保留 MiniMax MCP 的 understand_image（VLM）
                iu_provider = settings.image_understanding_provider
                if iu_provider == "baidu":
                    from backend.agent.tools.baidu_ocr import BaiduOcrTool
                    vision_tool = BaiduOcrTool()
                    self.tools[vision_tool.name] = vision_tool
                    logger.info("[BidReviewAgent.initialize] Overrode understand_image with Baidu OCR tool")
                elif iu_provider == "volcengine" or settings.llm_provider == "volcengine":
                    from backend.agent.tools.volcengine_vision import VolcengineVisionTool
                    vision_tool = VolcengineVisionTool()
                    self.tools[vision_tool.name] = vision_tool
                    logger.info("[BidReviewAgent.initialize] Overrode understand_image with Volcengine vision tool")
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

    def _wrap_llm_generate(self) -> None:
        """Wrap llm_client.generate to log request/response details."""
        import functools
        import logging
        import time

        logger = self._logger or logging.getLogger(__name__)
        original_generate = self.llm_client.generate
        self._original_llm_generate = original_generate
        agent_ref = self

        @functools.wraps(original_generate)
        async def wrapped_generate(*args, **kwargs):
            agent_ref._llm_call_count += 1
            call_index = agent_ref._llm_call_count
            call_start = time.perf_counter()
            call_timestamp = datetime.now().isoformat()

            messages = kwargs.get("messages") or (args[0] if args else [])
            tools = kwargs.get("tools") or []

            # Log request
            agent_ref._log_llm_request(call_index, messages, tools)

            try:
                # Distributed rate limit across all worker processes
                from backend.services.llm_rate_limiter import acquire_llm_rate_limit
                async with acquire_llm_rate_limit():
                    async with _get_llm_semaphore():
                        async with asyncio.timeout(180.0):
                            response = await original_generate(*args, **kwargs)
            except TimeoutError:
                latency_ms = int((time.perf_counter() - call_start) * 1000)
                logger.error(
                    f"[LLM Interaction #{call_index}] TIMEOUT after {latency_ms}ms "
                    f"(asyncio.timeout exceeded 180s)"
                )
                agent_ref._interactions_log.append({
                    "call_index": call_index,
                    "timestamp": call_timestamp,
                    "latency_ms": latency_ms,
                    "status": "timeout",
                    "error": "asyncio.timeout exceeded 180s",
                    "request": agent_ref._build_request_summary(messages, tools),
                })
                raise
            except Exception as e:
                latency_ms = int((time.perf_counter() - call_start) * 1000)

                # 检测 429 Rate Limit 错误并记录到 Redis 监控计数器
                error_str = str(e)
                status_code = getattr(
                    getattr(e, "response", None), "status_code", None
                ) or getattr(e, "status_code", None)
                if status_code == 429 or "429" in error_str or "rate_limit" in error_str.lower():
                    agent_ref._record_llm_429(call_index, latency_ms, error_str)
                    logger.error(
                        f"[LLM Interaction #{call_index}] RATE LIMITED (429) after {latency_ms}ms: {e}"
                    )

                logger.error(
                    f"[LLM Interaction #{call_index}] FAILED after {latency_ms}ms: {e}"
                )
                agent_ref._interactions_log.append({
                    "call_index": call_index,
                    "timestamp": call_timestamp,
                    "latency_ms": latency_ms,
                    "status": "error",
                    "error": str(e),
                    "request": agent_ref._build_request_summary(messages, tools),
                })
                raise

            latency_ms = int((time.perf_counter() - call_start) * 1000)

            # Update heartbeat after LLM call to keep agent alive
            await agent_ref._update_heartbeat()

            # Check cancel_event after LLM call completes (heartbeat may have
            # timed out while we were waiting for the LLM response)
            if agent_ref.cancel_event and agent_ref.cancel_event.is_set():
                logger.warning(f"[LLM Interaction #{call_index}] Cancel event detected after LLM response")
                raise asyncio.CancelledError("Task cancelled by heartbeat timeout")

            # Log response
            agent_ref._log_llm_response(call_index, response, latency_ms)

            # Determine step number from message count (approximation)
            step_number = call_index

            agent_ref._interactions_log.append({
                "call_index": call_index,
                "step_number": step_number,
                "timestamp": call_timestamp,
                "latency_ms": latency_ms,
                "status": "success",
                "request": agent_ref._build_request_summary(messages, tools),
                "response": agent_ref._build_response_detail(response),
            })

            # Write metrics to Redis for perf_monitor.py to read
            agent_ref._write_llm_metrics(call_index, latency_ms, response)

            return response

        self.llm_client.generate = wrapped_generate

    def _write_llm_metrics(self, call_index: int, latency_ms: int, response: Any) -> None:
        """Write LLM call metrics to Redis for external monitoring."""
        try:
            import redis as redis_lib
            r = redis_lib.Redis.from_url(
                settings.redis_url,
                max_connections=1,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
                decode_responses=True,
            )
            key = f"metrics:llm:{self.project_id}"
            existing = r.get(key)
            metrics = json.loads(existing) if existing else {
                "call_count": 0,
                "total_latency_ms": 0,
                "total_tokens": 0,
                "min_latency_ms": 999999,
                "max_latency_ms": 0,
                "start_time": datetime.now().isoformat(),
            }
            metrics["call_count"] += 1
            metrics["total_latency_ms"] += latency_ms
            metrics["min_latency_ms"] = min(metrics["min_latency_ms"], latency_ms)
            metrics["max_latency_ms"] = max(metrics["max_latency_ms"], latency_ms)
            metrics["last_latency_ms"] = latency_ms
            metrics["last_call_time"] = datetime.now().isoformat()
            # Extract token usage if available
            if hasattr(response, "usage") and response.usage:
                metrics["total_tokens"] += getattr(response.usage, "total_tokens", 0)
            r.set(key, json.dumps(metrics), ex=7200)  # TTL 2h
            r.close()
        except Exception:
            pass  # Metrics collection must not affect business logic

    def _record_llm_429(self, call_index: int, latency_ms: int, error_str: str) -> None:
        """Record LLM 429 rate limit errors to Redis for cluster-wide monitoring.

        Tracks 429 error count and timestamps so that operations can detect
        API rate limit saturation and trigger alerts.
        """
        try:
            import redis as redis_lib
            r = redis_lib.Redis.from_url(
                settings.redis_url,
                max_connections=1,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
                decode_responses=True,
            )
            now = datetime.now()

            # 全局 429 计数器（集群级，按小时滚动）
            hour_key = f"metrics:llm_429:{now.strftime('%Y%m%d%H')}"
            r.incr(hour_key)
            r.expire(hour_key, 86400)  # 保留 24 小时

            # 本项目的 429 日志（最近 100 条）
            log_key = f"metrics:llm_429_log:{self.project_id}"
            entry = json.dumps({
                "call_index": call_index,
                "timestamp": now.isoformat(),
                "latency_ms": latency_ms,
                "error": error_str[:500],
            })
            r.lpush(log_key, entry)
            r.ltrim(log_key, 0, 99)  # 保留最近 100 条
            r.expire(log_key, 3600)  # TTL 1 小时

            r.close()
        except Exception:
            pass  # 监控记录不能影响业务逻辑

    def _build_request_summary(self, messages: list, tools: list) -> dict:
        """Build a summary dict of the LLM request."""
        messages_summary = []
        for msg in messages:
            msg_info = {"role": msg.role}
            content = msg.content
            if isinstance(content, str):
                msg_info["content_length"] = len(content)
            elif isinstance(content, list):
                msg_info["content_length"] = sum(
                    len(str(b)) for b in content if isinstance(b, dict)
                )
            if hasattr(msg, "thinking") and msg.thinking:
                msg_info["thinking_length"] = len(msg.thinking)
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                msg_info["tool_calls"] = [
                    tc.function.name for tc in msg.tool_calls
                ]
            if hasattr(msg, "tool_call_id") and msg.tool_call_id:
                msg_info["tool_call_id"] = msg.tool_call_id
            messages_summary.append(msg_info)

        return {
            "message_count": len(messages),
            "messages_summary": messages_summary,
            "tool_names": [t.name for t in tools] if tools else [],
        }

    def _build_response_detail(self, response) -> dict:
        """Build a detail dict of the LLM response."""
        result = {
            "content": response.content or "",
            "finish_reason": response.finish_reason,
        }
        if response.thinking:
            result["thinking"] = response.thinking
        if response.tool_calls:
            result["tool_calls"] = [
                {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
                for tc in response.tool_calls
            ]
        if response.usage:
            result["usage"] = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        return result

    def _log_llm_request(self, call_index: int, messages: list, tools: list) -> None:
        """Log LLM request details."""
        import logging
        logger = self._logger or logging.getLogger(__name__)

        msg_count = len(messages)
        tool_names = [t.name for t in tools] if tools else []
        roles = [m.role for m in messages]

        logger.info(
            f"[LLM Interaction #{call_index}] >>> REQUEST: "
            f"messages={msg_count}, roles={roles}, tools={tool_names}"
        )
        for i, msg in enumerate(messages):
            content_len = len(msg.content) if isinstance(msg.content, str) else "?"
            tc_names = [tc.function.name for tc in msg.tool_calls] if msg.tool_calls else []
            logger.debug(
                f"[LLM Interaction #{call_index}] msg[{i}]: "
                f"role={msg.role}, content_len={content_len}"
                + (f", tool_calls={tc_names}" if tc_names else "")
                + (f", tool_call_id={msg.tool_call_id}" if msg.tool_call_id else "")
            )

    def _log_llm_response(self, call_index: int, response, latency_ms: int) -> None:
        """Log LLM response details."""
        import logging
        logger = self._logger or logging.getLogger(__name__)

        content_len = len(response.content) if response.content else 0
        thinking_len = len(response.thinking) if response.thinking else 0
        tc_names = [tc.function.name for tc in response.tool_calls] if response.tool_calls else []
        usage_info = ""
        if response.usage:
            usage_info = (
                f", tokens={response.usage.total_tokens}"
                f" (prompt={response.usage.prompt_tokens}"
                f", completion={response.usage.completion_tokens})"
            )

        logger.info(
            f"[LLM Interaction #{call_index}] <<< RESPONSE: "
            f"latency={latency_ms}ms, content_len={content_len}"
            f", thinking_len={thinking_len}"
            f", tool_calls={tc_names}"
            f", finish_reason={response.finish_reason}"
            f"{usage_info}"
        )

        # Log content preview
        if response.content:
            preview = response.content[:2000]
            logger.debug(
                f"[LLM Interaction #{call_index}] content:\n{preview}"
                + ("..." if len(response.content) > 2000 else "")
            )

        # Log thinking preview
        if response.thinking:
            preview = response.thinking[:2000]
            logger.debug(
                f"[LLM Interaction #{call_index}] thinking:\n{preview}"
                + ("..." if len(response.thinking) > 2000 else "")
            )

        # Log tool call arguments
        if response.tool_calls:
            for tc in response.tool_calls:
                logger.debug(
                    f"[LLM Interaction #{call_index}] tool_call: "
                    f"name={tc.function.name}, args={json.dumps(tc.function.arguments, ensure_ascii=False, default=str)}"
                )

    def _write_interaction_log(self) -> None:
        """Write accumulated LLM interaction log to a JSON file.

        Writes to {workspace}/{user_id}/{project_id}/logs/interaction_{timestamp}.jsonl
        """
        import logging
        logger = self._logger or logging.getLogger(__name__)

        if not self._interactions_log:
            logger.info("[BidReviewAgent._write_interaction_log] No interactions to log")
            return

        try:
            log_dir = settings.workspace_path / str(self.user_id) / str(self.project_id) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = log_dir / f"interaction_{timestamp}.jsonl"

            with open(log_path, "w", encoding="utf-8") as f:
                # Write header metadata
                header = {
                    "type": "session_meta",
                    "project_id": self.project_id,
                    "user_id": self.user_id,
                    "tender_docs": [
                        {"filename": fn, "path": p} for fn, p in (self.tender_docs or [])
                    ],
                    "bid_docs": [
                        {"filename": fn, "path": p} for fn, p in (self.bid_docs or [])
                    ],
                    "rule_doc": self.rule_doc_path,
                    "total_llm_calls": len(self._interactions_log),
                    "written_at": datetime.now().isoformat(),
                }
                f.write(json.dumps(header, ensure_ascii=False) + "\n")

                for interaction in self._interactions_log:
                    f.write(json.dumps(interaction, ensure_ascii=False, default=str) + "\n")

            logger.info(
                f"[BidReviewAgent._write_interaction_log] "
                f"Wrote {len(self._interactions_log)} interactions to {log_path}"
            )
        except Exception as e:
            logger.error(f"[BidReviewAgent._write_interaction_log] Failed: {e}")

    def _send_event(self, event_type: str, data: dict) -> None:
        """Send an event via callback if available."""
        import logging
        logger = self._logger or logging.getLogger(__name__)
        logger.debug(f"[BidReviewAgent._send_event] type={event_type}, data_keys={list(data.keys())}, callback_exists={self.event_callback is not None}")
        if self.event_callback:
            try:
                self.event_callback(event_type, data)
                logger.debug(f"[BidReviewAgent._send_event] Successfully sent event type={event_type}")
            except Exception as e:
                logger.error(f"[BidReviewAgent._send_event] Failed to send event: {e}")

    async def _update_heartbeat(self) -> None:
        """Update last_heartbeat in DB to signal agent is still alive.

        Called during agent execution (after LLM calls and step completions)
        to keep the heartbeat fresh independent of frontend connectivity.
        """
        if not self._task_id:
            return
        try:
            from sqlalchemy import update as sql_update
            from backend.models import ReviewTask
            session_factory = self._heartbeat_session_factory
            if session_factory is None:
                from backend.models.base import async_session_factory as session_factory
            async with session_factory() as db:
                await db.execute(
                    sql_update(ReviewTask)
                    .where(ReviewTask.id == self._task_id)
                    .values(last_heartbeat=datetime.utcnow())
                )
                await db.commit()
        except Exception as e:
            import logging
            (self._logger or logging.getLogger(__name__)).warning(
                f"[BidReviewAgent._update_heartbeat] Failed for task_id={self._task_id}: {e}",
                exc_info=True,
            )

    async def _check_heartbeat_async(self) -> bool:
        """Check if heartbeat has exceeded timeout or cancellation was requested.

        Returns:
            True if heartbeat is OK (within timeout or no task context),
            False if exceeded timeout or cancellation was requested.
        """
        import logging
        logger = self._logger or logging.getLogger(__name__)

        # No task context means we're in standalone mode without heartbeat tracking
        if not hasattr(self, '_task_id') or not self._task_id:
            return True

        # Check if cancellation was requested via API
        from backend.tasks.review_tasks import is_task_cancelled
        if is_task_cancelled(self._task_id):
            logger.warning("[BidReviewAgent] Cancellation requested via API")
            self._cancel_reason = "api_cancellation"
            return False

        from sqlalchemy import select
        from backend.models import ReviewTask
        session_factory = self._heartbeat_session_factory
        if session_factory is None:
            from backend.models.base import async_session_factory as session_factory

        try:
            async with session_factory() as db:
                result = await db.execute(
                    select(ReviewTask).where(ReviewTask.id == self._task_id)
                )
                task = result.scalar_one_or_none()
                if not task or not task.last_heartbeat:
                    self._heartbeat_fail_count = 0
                    return True  # No heartbeat yet, assume OK

                elapsed = (datetime.utcnow() - task.last_heartbeat).total_seconds()
                if elapsed > self.heartbeat_timeout:
                    logger.warning(
                        f"[BidReviewAgent] Heartbeat timeout: {elapsed:.1f}s > {self.heartbeat_timeout}s, "
                        f"task_id={self._task_id}, last_heartbeat={task.last_heartbeat}"
                    )
                    self._cancel_reason = "heartbeat_timeout"
                    return False
                self._heartbeat_fail_count = 0  # healthy check → reset fail counter
                return True
        except Exception as e:
            # Fail-closed with tolerance: a single DB hiccup must NOT kill a healthy task,
            # but sustained failures (>= threshold x 5s poll ≈ 15s) mean heartbeat tracking
            # itself is broken and the task cannot self-heal — terminate rather than run forever.
            self._heartbeat_fail_count += 1
            logger.warning(
                f"[BidReviewAgent] Heartbeat check failed "
                f"({self._heartbeat_fail_count}/{self._heartbeat_fail_threshold}) "
                f"for task_id={self._task_id}: {e}",
                exc_info=True,
            )
            if self._heartbeat_fail_count >= self._heartbeat_fail_threshold:
                logger.error(
                    f"[BidReviewAgent] Heartbeat check failing CLOSED after "
                    f"{self._heartbeat_fail_count} consecutive failures, task_id={self._task_id}"
                )
                self._cancel_reason = "heartbeat_check_failed"
                return False
            return True  # transient failure — tolerate and retry on the next poll

    async def _heartbeat_monitor_loop(self) -> None:
        """Background loop that monitors heartbeat and sets cancel_event on timeout."""
        import logging
        logger = self._logger or logging.getLogger(__name__)

        while not self.cancel_event.is_set():
            await asyncio.sleep(5)  # Check every 5 seconds

            if self.cancel_event.is_set():
                break

            if not await self._check_heartbeat_async():
                logger.warning("[BidReviewAgent] Setting cancel_event due to heartbeat timeout or API cancellation")
                self.cancel_event.set()
                break

    def _extract_tool_signature(self, tool_call: dict) -> tuple[str, str]:
        """Extract a (tool_name, key_arg) signature for duplicate detection.

        Args:
            tool_call: Dict with 'name' and 'arguments' keys.

        Returns:
            Tuple of (tool_name, key_arg) where key_arg is the most relevant
            parameter for identifying repeated operations.
        """
        name = tool_call.get("name", "")
        arguments = tool_call.get("arguments") or {}

        # Extract the most relevant parameter per tool
        if name == "search_tender_doc":
            key_arg = arguments.get("query", "")
        elif name == "get_document_toc":
            key_arg = arguments.get("doc_type", "")
        elif name == "get_section_content":
            key_arg = arguments.get("section_id", "")
        elif name == "understand_image":
            key_arg = arguments.get("image_path", "")
        else:
            key_arg = ""

        return (name, str(key_arg))

    def _check_duplicate_actions(self, tool_calls: list[dict]) -> Optional[str]:
        """Check for repeated tool calls and return a warning message if detected.

        Detects two patterns:
        1. Same (tool_name, key_arg) pair appearing ≥3 times in recent history
        2. Same tool_name accumulating too many total calls (≥3 for get_document_toc)

        Args:
            tool_calls: List of tool call dicts from the current step.

        Returns:
            Warning message string if duplicates detected, None otherwise.
        """
        if self._write_file_called:
            return None

        # Record current step's tool calls
        for tc in tool_calls:
            sig = self._extract_tool_signature(tc)
            self._tool_call_history.append(sig)

        if not self._tool_call_history:
            return None

        # --- Pattern 1: Same exact (tool, key_arg) repeated ≥3 times ---
        history_list = list(self._tool_call_history)
        from collections import Counter
        sig_counts = Counter(history_list)

        for (name, key_arg), count in sig_counts.items():
            if count >= 3 and name in (
                "search_tender_doc", "get_document_toc",
                "get_section_content", "understand_image",
            ):
                # Only warn for search-type tools with meaningful key_arg
                if key_arg:
                    display_arg = key_arg if len(key_arg) <= 30 else key_arg[:27] + "..."
                    return (
                        f"你已经多次使用相同参数调用 {name}（"
                        f"参数 '{display_arg}' 已执行 {count} 次），"
                        f"这些内容已经检索过了，无需重复搜索。"
                        f"请停止重复搜索，基于已有信息进行分析，"
                        f"并调用 write_file 写出审查结果。"
                    )

        # --- Pattern 2: Single tool called too many times overall ---
        tool_total_counts = Counter(name for name, _ in history_list)
        tool_limits = {
            "get_document_toc": 3,
            "get_section_images": 5,
        }
        for tool_name, limit in tool_limits.items():
            if tool_total_counts.get(tool_name, 0) >= limit:
                count = tool_total_counts[tool_name]
                return (
                    f"你已经多次调用 {tool_name}（{count} 次），"
                    f"文档结构不会改变，无需重复获取。"
                    f"请直接基于已有信息继续工作。"
                )

        return None

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
            logger.debug(f"[BidReviewAgent._emit_event] step_start for step {step}")

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
                # Track if write_file has been called - suppresses future duplicate warnings
                for tc in data.get("tool_calls", []) or []:
                    if tc.get("name") == "write_file":
                        self._write_file_called = True
                        break
                logger.debug(f"[BidReviewAgent._emit_event] llm_output for step {step}, tool_calls_count={len(data.get('tool_calls', []))}")

        elif event_type == "tool_call_start":
            # Tool call started - tool_calls already accumulated from llm_output
            step = data.get("step", 0)
            if step in self._step_data:
                logger.debug(f"[BidReviewAgent._emit_event] tool_call_start for step {step}, tool={data.get('tool')}")

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
                logger.debug(f"[BidReviewAgent._emit_event] tool_call_end for step {step}, tool={data.get('tool')}, success={data.get('success')}, result={result_preview}")

        elif event_type == "step_complete":
            # Emit consolidated sub_agent_step event
            step = data.get("step", 0)
            self._total_steps += 1
            captured_step_tool_calls: list[dict] = []
            if step in self._step_data:
                step_info = self._step_data[step]
                # Capture tool_calls for duplicate detection before cleanup
                captured_step_tool_calls = list(step_info.get("tool_calls", []))
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
                    logger.debug(f"[BidReviewAgent._emit_event] step_complete step={step}, tool_result[{i}]: name={tr['name']}, result_keys={list(tr['result'].keys())}, content_len={len(str(tr['result'].get('content', '')))}")

                # Send consolidated event via callback
                if self.event_callback:
                    try:
                        self.event_callback("sub_agent_step", consolidated_event)
                        logger.debug(f"[BidReviewAgent._emit_event] Emitted sub_agent_step for step {step}")
                    except Exception as e:
                        logger.error(f"[BidReviewAgent._emit_event] Failed to emit sub_agent_step: {e}")

                # Clean up
                del self._step_data[step]

            # Update heartbeat after each step to keep agent alive
            if self._task_id:
                try:
                    hb_task = asyncio.ensure_future(self._update_heartbeat())
                    hb_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
                except Exception as e:
                    import logging
                    (self._logger or logging.getLogger(__name__)).warning(
                        f"[BidReviewAgent._emit_event] Failed to schedule heartbeat update "
                        f"for task_id={self._task_id}: {e}",
                        exc_info=True,
                    )

            logger.debug(f"[BidReviewAgent._emit_event] step_complete for step {step}")

            # Duplicate action detection: check and inject warning if needed
            if captured_step_tool_calls:
                warning = self._check_duplicate_actions(captured_step_tool_calls)
                if warning:
                    self._duplicate_warning_count += 1
                    logger.warning(f"[BidReviewAgent] Duplicate action detected (warning #{self._duplicate_warning_count}): {warning[:80]}")
                    # Inject warning as a user message to guide the LLM
                    self.messages.append(Message(
                        role="user",
                        content=f"[系统提醒] {warning}",
                    ))

        elif event_type == "completed":
            # Agent completed - forward to callback
            if self.event_callback:
                try:
                    self.event_callback("completed", data)
                    logger.debug(f"[BidReviewAgent._emit_event] Forwarded completed event")
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

    def _parse_check_items(self, rule_doc_content: str) -> list[dict]:
        """Parse check items from rule document to extract names and suggested keywords.

        Args:
            rule_doc_content: Full text content of the rule document.

        Returns:
            List of dicts with keys: index, name, suggested_keywords
        """
        items = []
        # Accept variable hash counts (##, ####, #####) for compatibility with different rule file formats
        sections = re.split(r'(?=##+\s*检查项\d+)', rule_doc_content)

        for section in sections:
            if not section.strip():
                continue

            index_match = re.match(r'##+\s*检查项(\d+)', section)
            if not index_match:
                continue
            index = int(index_match.group(1))

            name_match = re.search(r'##+\s*检查项名称\s*\n\s*(.+)', section)
            name = name_match.group(1).strip() if name_match else f"检查项{index}"

            # Derive suggested search keywords from the name
            suggested_keywords = [name.replace("检查", "")]
            for kw in re.findall(r'[一-鿿]{2,}', name):
                if kw not in suggested_keywords:
                    suggested_keywords.append(kw)

            items.append({
                "index": index,
                "name": name,
                "suggested_keywords": suggested_keywords[:3],
            })

        return items

    def _build_system_prompt(self, rule_doc_content: str, experience_skills: list[dict] | None = None) -> str:
        """Build system prompt containing rule document content.

        Args:
            rule_doc_content: Full text content of the rule document.
            experience_skills: Optional list of experience skills to inject.

        Returns:
            System prompt string with rule content embedded.
        """
        inventory_parts = []
        tender_names = [name for name, _ in self.tender_docs]
        bid_names = [name for name, _ in self.bid_docs]
        inventory_parts.append(f"招标文件（共 {len(tender_names)} 份）：")
        for i, name in enumerate(tender_names, 1):
            inventory_parts.append(f"  {i}. {name}")
        inventory_parts.append(f"投标文件（共 {len(bid_names)} 份）：")
        for i, name in enumerate(bid_names, 1):
            inventory_parts.append(f"  {i}. {name}")
        doc_inventory = "\n".join(inventory_parts)

        image_dir_parts = []
        for name, path in self.tender_docs:
            img_dir = str(Path(path).parent)
            image_dir_parts.append(f"  招标书 \"{name}\" → {img_dir}/{{image_path}}")
        for name, path in self.bid_docs:
            img_dir = str(Path(path).parent)
            image_dir_parts.append(f"  投标书 \"{name}\" → {img_dir}/{{image_path}}")
        image_directory_map = "\n".join(image_dir_parts)

        experience_guidance = ""
        if experience_skills:
            experience_guidance = self._render_experience_guidance(experience_skills)

        return SYSTEM_PROMPT_WITH_RULE.format(
            rule_doc_content=rule_doc_content,
            doc_inventory=doc_inventory,
            image_directory_map=image_directory_map,
            experience_guidance=experience_guidance,
        )

    def _render_experience_guidance(self, skills: list[dict]) -> str:
        if not skills:
            return ""

        parts = [
            "> 以下经验来自历史审查案例，由系统自动提炼。",
            "> 请参考但不盲从——若与本次实际情况冲突，以本次为准。",
            "",
        ]
        for i, skill in enumerate(skills[:3], 1):
            form_hint = ""
            if skill.get("skill_form") == "hypothesis":
                form_hint = "（来自失败案例，谨慎参考）"
            parts.append(
                f"### 经验 {i}：{skill['name']}"
                f"（置信度 {skill['confidence']:.2f}，"
                f"成熟度 {skill['maturity_score']:.2f}）{form_hint}"
            )
            parts.append(skill.get("description", ""))
            content = skill.get("content", "")
            if len(content) > 150:
                last_period = content[:150].rfind("。")
                if last_period > 50:
                    content = content[: last_period + 1]
                else:
                    content = content[:150] + "..."
            parts.append(content)
            parts.append("")

        return "\n".join(parts)

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
        logger.info(f"[BidReviewAgent.run_review] Starting, tender_docs={self.tender_docs}, bid_docs={self.bid_docs}, rule_doc={self.rule_doc_path}")

        try:
            # 1. Read rule file
            rule_doc_content = self._load_rule_doc()
            logger.info(f"[BidReviewAgent.run_review] Rule doc loaded, size={len(rule_doc_content)} chars")

            # 2. Retrieve experience skills (if enabled)
            experience_skills = None
            if get_settings().experience_injection_enabled:
                try:
                    from backend.experience.retriever import ExperienceRetriever
                    retriever = ExperienceRetriever()
                    group_id = Path(self.rule_doc_path).stem
                    experience_skills = await retriever.retrieve(
                        group_id=group_id,
                        query=rule_doc_content[:500],
                    )
                    if experience_skills:
                        logger.info(f"[BidReviewAgent.run_review] Injected {len(experience_skills)} experience skills")
                except Exception as e:
                    logger.warning(f"Experience retrieval failed: {e}")

            # 3. Build system prompt with experience
            system_prompt = self._build_system_prompt(rule_doc_content, experience_skills)
            self.system_prompt = system_prompt
            # Update system message in messages list
            self.messages[0] = Message(role="system", content=system_prompt)
            logger.info(f"[BidReviewAgent.run_review] System prompt built, size={len(system_prompt)} chars")

            # Log full system prompt (DEBUG: large content, needed only for deep debugging)
            logger.debug(f"[BidReviewAgent.run_review] === SYSTEM PROMPT START ===")
            logger.debug(f"\n{system_prompt}")
            logger.debug(f"[BidReviewAgent.run_review] === SYSTEM PROMPT END ===")

            # 3. Build task prompt with output md path
            # Include task_id + rule_stem to avoid collision:
            # - Parallel sub-agents in same task: different rule_stem
            # - Same project, different re-reviews: different task_id
            task_id = getattr(self, "_task_id", None) or int(time.time())
            rule_stem = Path(self.rule_doc_path).stem
            output_md_path = str(self.workspace_dir / f"review_{task_id}_{rule_stem}.md")
            self._output_md_path = output_md_path

            # Parse check items for explicit enumeration in task prompt
            check_items = self._parse_check_items(rule_doc_content)
            self._parsed_check_items = check_items  # 保存供后续写入数据库
            self._rule_doc_name = Path(self.rule_doc_path).name
            self._check_item_name = check_items[0]["name"] if check_items else None
            check_list_lines = []
            for item in check_items:
                keywords_hint = "、".join(item["suggested_keywords"][:2])
                check_list_lines.append(
                    f"  {item['index']}. {item['name']} — 搜索关键词: {keywords_hint}"
                )
            check_list_str = "\n".join(check_list_lines)
            total_items = len(check_items) if check_items else "所有"

            # Build document inventory listings (multi-document: list of (filename, path))
            def _fmt_doc_inventory(docs: list[tuple[str, str]] | None) -> str:
                if not docs:
                    return "  （无）"
                lines = []
                for fn, p in docs:
                    lines.append(f"  - {fn} → {p}")
                return "\n".join(lines)

            tender_inventory = _fmt_doc_inventory(self.tender_docs)
            bid_inventory = _fmt_doc_inventory(self.bid_docs)

            task = f"""请执行投标文件审查任务。

文档信息：
- 招标书（共 {len(self.tender_docs or [])} 份）：
{tender_inventory}
- 投标书（共 {len(self.bid_docs or [])} 份）：
{bid_inventory}
- 审查结果输出文件: {output_md_path}

共有 {total_items} 个检查项：
{check_list_str}

执行要求：
对上述每一个检查项，你必须：
1. 调用 search_tender_doc(文档类型="tender", query="关键词") 查找招标书中的要求
2. 调用 search_tender_doc(文档类型="bid", query="关键词") 查找投标书中所有对应位置
3. 如需精确判断，调用 compare_bid(requirement=..., bid_content=...) 进行深入比对
4. 如搜索结果含 image_refs，优先使用 understand_image 分析图片；understand_image 失败时再用 get_image_ocr
5. 如果 get_section_images 返回"无图片"，不要立即判定为无图片。改用 search_tender_doc(query="相关关键词") 搜索，结果可能包含 image_refs
6. 记录结果后，继续下一个检查项

所有 {total_items} 个检查项完成后，调用 write_file 将完整结果写入 {output_md_path}。

⚠️ 重要约束：
- 禁止使用 read_file 读取招标书或投标书（必须使用 search_tender_doc）
- 禁止在一次工具调用中尝试完成多个检查项
- 必须逐项检查，每个检查项至少调用 search_tender_doc 两次
- 必须使用 write_file 写入最终结果
- 图片分析优先使用 understand_image（VLM 视觉理解），understand_image 失败时再用 get_image_ocr
- understand_image 的 prompt 中禁止使用"身份证""身份证号码""姓名""护照号码"等敏感词，使用"证件""证件编号""人员名称"等替代
- 如 understand_image 报错（如 1026 敏感内容），回退使用 get_image_ocr 继续审查"""
            self.add_user_message(task)
            logger.info(f"[BidReviewAgent.run_review] Task prompt added, output_md_path={output_md_path}")

            # Log full task prompt (DEBUG: large content, needed only for deep debugging)
            logger.debug(f"[BidReviewAgent.run_review] === TASK PROMPT START ===")
            logger.debug(f"\n{task}")
            logger.debug(f"[BidReviewAgent.run_review] === TASK PROMPT END ===")

            # Create cancel event if not provided
            if self.cancel_event is None:
                self.cancel_event = asyncio.Event()
            elif self.cancel_event.is_set():
                # Cancel event is shared across sub-agents — if already set,
                # propagation from another sub-agent is in progress. Keep it
                # set so this agent also stops immediately.
                logger.warning("[BidReviewAgent.run_review] cancel_event already set, will stop immediately")

            # Start heartbeat monitor as background task
            heartbeat_monitor = asyncio.create_task(
                self._heartbeat_monitor_loop()
            )

            try:
                # 4. Call base class run() - reuses Mini-Agent loop with event_callback
                run_result = await self.run(cancel_event=self.cancel_event)
                logger.info(f"[BidReviewAgent.run_review] Agent.run() completed, result_preview={str(run_result)[:200] if run_result else 'None'}")

                # Check run result for error/cancellation/max-steps conditions
                is_normal = True
                if run_result is None:
                    logger.error("[BidReviewAgent.run_review] Agent.run() returned None")
                    is_normal = False
                elif run_result.startswith("LLM call failed"):
                    logger.error(f"[BidReviewAgent.run_review] {run_result}")
                    is_normal = False
                elif run_result == "Task cancelled by user.":
                    logger.warning("[BidReviewAgent.run_review] Agent execution was cancelled by user")
                    is_normal = False
                elif run_result.startswith("Task couldn't be completed"):
                    logger.warning(f"[BidReviewAgent.run_review] {run_result}")
                    self._max_steps_exceeded = True
                    is_normal = False
            finally:
                heartbeat_monitor.cancel()
                try:
                    await heartbeat_monitor
                except asyncio.CancelledError:
                    pass

                # Write interaction log even if heartbeat monitor was cancelled
                self._write_interaction_log()

            # Log full message history (DEBUG: largest per-run volume source —
            # 5KB content + 2KB thinking preview per message. Needed only for
            # deep debugging; full content also preserved in sub_agent_*.log
            # and the interaction JSON log.)
            logger.debug(f"[BidReviewAgent.run_review] === FULL MESSAGE HISTORY START ===")
            for i, msg in enumerate(self.messages):
                msg_header = f"[Message {i}] role={msg.role}"
                if msg.thinking:
                    msg_header += f", thinking_length={len(msg.thinking)}"
                if msg.tool_calls:
                    msg_header += f", tool_calls={[tc.function.name for tc in msg.tool_calls]}"
                if msg.tool_call_id:
                    msg_header += f", tool_call_id={msg.tool_call_id}"
                logger.debug(f"[BidReviewAgent.run_review] {msg_header}")
                if msg.content:
                    content_preview = msg.content[:5000] + "..." if len(msg.content) > 5000 else msg.content
                    logger.debug(f"[BidReviewAgent.run_review] [Message {i}] content:\n{content_preview}")
                if msg.thinking:
                    thinking_preview = msg.thinking[:2000] + "..." if len(msg.thinking) > 2000 else msg.thinking
                    logger.debug(f"[BidReviewAgent.run_review] [Message {i}] thinking:\n{thinking_preview}")
            logger.debug(f"[BidReviewAgent.run_review] === FULL MESSAGE HISTORY END ===")

            # 5. Post-process: extract findings from md file
            # Log whether WriteTool was ever called (diagnostic)
            write_tool_called = any(
                tc.function.name == "write_file"
                for msg in self.messages
                if msg.role == "assistant" and msg.tool_calls
                for tc in msg.tool_calls
            )
            if not write_tool_called:
                logger.error("[BidReviewAgent.run_review] CRITICAL: WriteTool was NEVER called during the entire execution")

            # Skip post_process for non-recoverable failures (LLM error, cancellation)
            if not is_normal:
                logger.warning("[BidReviewAgent.run_review] Agent did not complete normally, skipping _post_process, trying fallback extraction")
                findings = self._extract_findings_from_messages()
                findings = self._enrich_findings(findings)
                logger.info(f"[BidReviewAgent.run_review] Fallback extraction returned {len(findings)} findings")
                return findings

            # Normal or max-steps: try post_process first
            findings = await self._post_process(output_md_path)
            logger.info(f"[BidReviewAgent.run_review] Post-processing completed, found {len(findings)} findings")

            # Fallback: if post_process returned empty, try extracting from message history
            if not findings:
                logger.warning("[BidReviewAgent.run_review] _post_process returned empty, trying _extract_findings_from_messages")
                findings = self._extract_findings_from_messages()
                logger.info(f"[BidReviewAgent.run_review] Fallback extraction returned {len(findings)} findings")

            return self._enrich_findings(findings)

        except Exception as e:
            logger.exception(f"[BidReviewAgent.run_review] Exception: {e}")
            self._write_interaction_log()
            return []

    def _enrich_findings(self, findings: list[dict]) -> list[dict]:
        for f in findings:
            if "rule_doc_name" not in f:
                f["rule_doc_name"] = getattr(self, "_rule_doc_name", None)
            if "check_item_name" not in f:
                f["check_item_name"] = getattr(self, "_check_item_name", None)
        return findings

    def _extract_findings_from_messages(self) -> list[dict]:
        """Extract structured findings from agent message history.

        Returns:
            List of structured findings if found, empty list otherwise.
        """
        # Phase 1: Try JSON extraction from message content
        findings = self._extract_json_findings()
        if findings:
            return findings

        # Phase 2: Keyword-based fallback extraction for non-JSON analysis text
        return self._extract_keyword_findings()

    def _extract_json_findings(self) -> list[dict]:
        """Extract findings from JSON-formatted content in agent messages."""
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

    # Keywords indicating non-compliance in Chinese review analysis text
    _NON_COMPLIANCE_KEYWORDS = [
        "不通过", "不符合", "不合规", "未提供", "缺失", "缺少",
        "存在问题", "不满足", "未满足", "没有找到", "未找到",
        "未包含", "未说明", "未明确", "未体现", "不一致",
    ]

    def _extract_keyword_findings(self) -> list[dict]:
        """Fallback: extract findings from natural-language analysis text.

        Scans assistant messages for non-compliance indicators and constructs
        semi-structured findings from surrounding context.
        """
        findings = []
        requirement_counter = 1
        seen_contexts: set[str] = set()

        for msg in reversed(self.messages):
            if msg.role != "assistant" or not msg.content:
                continue

            content = msg.content
            # Skip if content looks like JSON (already handled by _extract_json_findings)
            stripped = content.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                continue

            # Search for non-compliance patterns in the text
            for keyword in self._NON_COMPLIANCE_KEYWORDS:
                idx = content.find(keyword)
                if idx < 0:
                    continue

                # Extract surrounding context (~200 chars around the keyword)
                start = max(0, idx - 80)
                end = min(len(content), idx + len(keyword) + 120)
                context = content[start:end].strip()

                # Deduplicate by context to avoid repeating the same finding
                if context in seen_contexts:
                    continue
                seen_contexts.add(context)

                # Try to identify the requirement being discussed
                requirement_content = self._extract_requirement_context(
                    content, idx
                )

                findings.append({
                    "requirement_key": f"req_{requirement_counter:03d}",
                    "requirement_content": requirement_content or context[:200],
                    "bid_content": None,
                    "is_compliant": False,
                    "severity": "major",
                    "location_page": None,
                    "location_line": None,
                    "suggestion": None,
                    "explanation": context,
                })
                requirement_counter += 1

        return findings

    def _extract_requirement_context(self, content: str, keyword_idx: int) -> str:
        """Extract the requirement description preceding a keyword match."""
        # Look backwards from the keyword for a numbered item or section indicator
        prefix = content[:keyword_idx]

        # Try to find patterns like "1.", "2.", "检查项X:", "要求X:"
        patterns = [
            r'(?:^|\n)\s*(?:\d+[\.\、\)]|检查项|要求|第\d+条)\s*[^\n]{10,200}',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, prefix)
            if matches:
                return matches[-1].strip()

        # Fallback: return the sentence containing or before the keyword
        sentences = re.split(r'[。；\n]', prefix)
        if sentences:
            return sentences[-1].strip()[:300]

        return prefix[-300:] if len(prefix) > 300 else prefix

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
        """Close MCP connections and cleanup resources.

        Only calls cleanup_mcp_connections() when _owns_mcp_cleanup is True.
        Sub-agents set this to False to avoid killing shared MCP connections
        of other concurrently running sub-agents.
        """
        if self._owns_mcp_cleanup:
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
                async with asyncio.timeout(300.0):
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
