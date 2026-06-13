"""SubAgentExecutor - 子代理执行器 - 管理单个子代理的生命周期."""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable
from backend.models.todo_item import TodoItem
from backend.agent.bid_review_agent import BidReviewAgent


def setup_sub_agent_logger(todo_id: str, log_dir: Path) -> logging.Logger:
    """为子代理设置专用 logger，输出到文件.

    Args:
        todo_id: TodoItem ID (UUID)
        log_dir: 日志目录路径

    Returns:
        配置好的 logger 实例
    """
    logger = logging.getLogger(f"sub_agent.{todo_id}")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()  # 避免重复 handler

    # 文件 handler
    fh = logging.FileHandler(log_dir / f"sub_agent_{todo_id}.log")
    fh.setLevel(logging.DEBUG)

    # 格式
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    # 子代理 logger 自有 per-todo 文件 handler，关闭向 root 传播，避免 DEBUG 行
    # 重复写入 celery_review.log（主 worker 日志）。调试信息仍保留在本文件中。
    logger.propagate = False
    return logger


class SubAgentExecutor:
    """子代理执行器 - 管理单个子代理的生命周期."""

    def __init__(
        self,
        todo_item: TodoItem,
        tender_docs: list[tuple[str, str]],
        bid_docs: list[tuple[str, str]],
        user_id: str,
        session_factory,
        event_callback: Optional[Callable] = None,
        session_id: Optional[str] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ):
        self.todo_item = todo_item
        self.tender_docs = tender_docs
        self.bid_docs = bid_docs
        self.user_id = user_id
        self.session_factory = session_factory
        self.event_callback = event_callback
        self.session_id = session_id
        self.cancel_event = cancel_event
        self._agent: Optional[BidReviewAgent] = None

    def _send_event(self, event_type: str, data: dict):
        """发送 SSE 事件，并写入数据库."""
        logger = logging.getLogger(__name__)
        # 如果是 sub_agent_step 事件，异步写入 AgentStep 表
        if event_type == "sub_agent_step":
            self._record_agent_step(data)

        if self.event_callback:
            try:
                self.event_callback(event_type, data)
            except Exception as e:
                logger.warning(f"[_send_event] Event callback failed: event_type={event_type}, error={e}")

    def _record_agent_step(self, data: dict) -> None:
        """将 AgentStep 写入数据库.

        优先通过当前事件循环的 create_task() 在主循环中异步执行，
        避免创建新的引擎/线程/事件循环。仅在无运行循环时回退。
        """
        import logging
        logger = logging.getLogger(__name__)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._record_agent_step_async(data))
            logger.info(f"[_record_agent_step] Scheduled step {data.get('step_number')} for todo {self.todo_item.id} via create_task")
        except RuntimeError:
            # No running loop — this shouldn't happen in normal flow, but handle gracefully
            logger.warning(f"[_record_agent_step] No running event loop, skipping step {data.get('step_number')}")
        except Exception as e:
            logger.error(f"[_record_agent_step] Failed to record step: {e}")

    async def _record_agent_step_async(self, data: dict) -> None:
        """异步写入 AgentStep 到数据库。"""
        from backend.models.agent_step import AgentStep

        async with self.session_factory() as db:
            step = AgentStep(
                todo_id=self.todo_item.id,
                step_number=data.get("step_number", 0),
                step_type=data.get("step_type", "unknown"),
                content=data.get("content", "") or "",
                tool_name=None,  # sub_agent_step 不使用此字段
                tool_args={"tool_calls": data.get("tool_calls", [])} if data.get("tool_calls") else None,
                tool_result={"tool_results": data.get("tool_results", [])} if data.get("tool_results") else None,
            )
            db.add(step)
            await db.commit()

    async def create_agent(self, max_steps: int = 100, logger=None) -> BidReviewAgent:
        """创建子代理实例.

        Args:
            max_steps: Maximum number of agent steps to run.
            logger: Optional logger for file output.
        """
        from backend.config import get_settings

        agent = BidReviewAgent(
            project_id=self.todo_item.project_id,
            tender_docs=self.tender_docs,
            bid_docs=self.bid_docs,
            user_id=self.user_id,
            rule_doc_path=self.todo_item.rule_doc_path,
            event_callback=self.event_callback,
            logger=logger,
            max_steps=max_steps,
            cancel_event=self.cancel_event,
            heartbeat_timeout=get_settings().sub_agent_heartbeat_timeout,
            heartbeat_session_factory=self.session_factory,
        )
        # Set task_id for heartbeat tracking (ReviewTask.id, not TodoItem.id)
        agent._task_id = self.session_id
        await agent.initialize()
        # Sub-agents must not call cleanup_mcp_connections() on close,
        # because it's a global function that would kill MCP connections
        # of other concurrently running sub-agents.
        agent._owns_mcp_cleanup = False
        self._agent = agent
        return agent

    async def execute(self, max_steps: int = 100) -> dict:
        """执行子代理检查任务."""
        import logging
        from backend.config import get_settings
        settings = get_settings()

        # 设置日志目录和 logger
        log_dir = settings.workspace_path / str(self.user_id) / str(self.todo_item.project_id) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        logger = setup_sub_agent_logger(self.todo_item.id, log_dir)
        logger.info(f"[SubAgentExecutor] Starting execution for todo_id={self.todo_item.id}, rule_doc={self.todo_item.rule_doc_path}")

        try:
            # 发送开始事件
            self._send_event("sub_agent_started", {
                "todo_id": self.todo_item.id,
                "rule_doc_name": self.todo_item.rule_doc_name,
                "max_steps": max_steps,
            })

            # 创建 agent (rule_doc_path 从 todo_item 获取，logger 传递用于文件输出)
            agent = await self.create_agent(max_steps=max_steps, logger=logger)

            # 执行检查 (task message is added inside run_review())
            findings = await agent.run_review()

            # Check if max_steps was exceeded — fail fast, no partial results
            if getattr(self._agent, '_max_steps_exceeded', False):
                actual_steps = self._agent._total_steps if self._agent else 0
                logger.warning(f"[SubAgentExecutor.execute] Max steps exceeded for todo_id={self.todo_item.id}, steps={actual_steps}/{max_steps}")
                return {
                    "success": False,
                    "error": f"Max steps exceeded ({actual_steps}/{max_steps})",
                    "todo_id": self.todo_item.id,
                    "actual_steps": actual_steps,
                    "brain_capacity": 100.0,
                }

            check_items = getattr(self._agent, '_parsed_check_items', [])
            logger.info(f"[SubAgentExecutor.execute] Findings: {findings}, check_items_count={len(check_items)}")

            # Calculate actual steps and brain capacity
            actual_steps = self._agent._total_steps if self._agent else 0
            brain_capacity = min(round(actual_steps / max_steps * 100, 1), 100.0) if max_steps > 0 else 0.0

            # 检查取消状态 — run_review() 可能因 heartbeat monitor 取消而返回部分结果
            if self.cancel_event and self.cancel_event.is_set():
                cancel_reason = getattr(self._agent, '_cancel_reason', None) or 'unknown'
                error_msg = (
                    "Task cancelled: heartbeat timeout (no progress detected)"
                    if cancel_reason == "heartbeat_timeout"
                    else "Task cancelled by user"
                )
                logger.warning(f"[SubAgentExecutor.execute] Cancelled for todo_id={self.todo_item.id}, reason={cancel_reason}")
                return {
                    "success": False,
                    "error": error_msg,
                    "todo_id": self.todo_item.id,
                    "actual_steps": actual_steps,
                    "brain_capacity": brain_capacity,
                    "_cancel_reason": cancel_reason,
                }

            # Capture diagnostics: verify write_file was both called AND
            # returned a result (role="tool" with matching tool_call_id)
            write_file_called = False
            write_file_call_ids = set()
            for msg in self._agent.messages:
                if msg.role == "assistant" and msg.tool_calls:
                    for tc in msg.tool_calls:
                        if tc.function.name == "write_file" and tc.id:
                            write_file_call_ids.add(tc.id)
            if write_file_call_ids:
                for msg in self._agent.messages:
                    if msg.role == "tool" and msg.tool_call_id in write_file_call_ids:
                        write_file_called = True
                        break

            return {
                "success": True,
                "findings": findings,
                "check_items": check_items,
                "todo_id": self.todo_item.id,
                "actual_steps": actual_steps,
                "brain_capacity": brain_capacity,
                "report_path": getattr(self._agent, '_output_md_path', None),
                "_diagnostics": {
                    "write_file_called": write_file_called,
                },
            }

        except Exception as e:
            logger.exception(f"[SubAgentExecutor.execute] Exception: {e}")
            # Diagnostic: log last few assistant messages for troubleshooting
            if self._agent and hasattr(self._agent, 'messages'):
                try:
                    last_msgs = self._agent.messages[-5:] if len(self._agent.messages) > 5 else self._agent.messages
                    for msg in last_msgs:
                        if msg.role == "assistant":
                            tc_names = [tc.function.name for tc in (msg.tool_calls or [])]
                            logger.warning(f"[SubAgentExecutor.execute] Last assistant msg: content_len={len(msg.content or '')}, tool_calls={tc_names}")
                except Exception as log_e:
                    logger.warning(f"[SubAgentExecutor.execute] Could not log agent diagnostics: {log_e}")
            return {
                "success": False,
                "error": str(e),
                "todo_id": self.todo_item.id,
            }
        finally:
            if self._agent:
                await self._agent.close()

    async def close(self):
        """关闭子代理."""
        if self._agent:
            await self._agent.close()
            self._agent = None


def detect_anomaly(result: dict, todo_item: TodoItem) -> bool:
    """检测结果是否异常.

    区分两种情况：
    - 全部合规（write_file 被调用，findings 为空）→ 不视为异常
    - 执行失败（write_file 未被调用，findings 为空）→ 视为异常
    """
    if not result:
        return True

    if not result.get("success", False):
        return True

    findings = result.get("findings", [])
    if not findings:
        diagnostics = result.get("_diagnostics", {})
        # If write_file was called, the agent completed its review and
        # produced output — empty findings means all items are compliant.
        if diagnostics.get("write_file_called", False):
            return False
        # write_file was never called — the agent failed to complete.
        return True

    return False