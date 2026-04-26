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
    return logger


class SubAgentExecutor:
    """子代理执行器 - 管理单个子代理的生命周期."""

    def __init__(
        self,
        todo_item: TodoItem,
        tender_doc_path: str,
        bid_doc_path: str,
        user_id: str,
        session_factory,
        event_callback: Optional[Callable] = None,
        session_id: Optional[str] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ):
        self.todo_item = todo_item
        self.tender_doc_path = tender_doc_path
        self.bid_doc_path = bid_doc_path
        self.user_id = user_id
        self.session_factory = session_factory
        self.event_callback = event_callback
        self.session_id = session_id
        self.cancel_event = cancel_event
        self._agent: Optional[BidReviewAgent] = None

    def _send_event(self, event_type: str, data: dict):
        """发送 SSE 事件，并写入数据库."""
        logger = logging.getLogger(__name__)
        # 如果是 sub_agent_step 事件，同步写入 AgentStep 表
        if event_type == "sub_agent_step":
            self._record_agent_step(data)

        if self.event_callback:
            try:
                self.event_callback(event_type, data)
            except Exception as e:
                logger.warning(f"[_send_event] Event callback failed: event_type={event_type}, error={e}")

    def _record_agent_step(self, data: dict) -> None:
        """同步写入 AgentStep 到数据库."""
        import concurrent.futures
        import logging
        from backend.models.agent_step import AgentStep

        logger = logging.getLogger(__name__)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, self._record_agent_step_async(data))
                future.result()
            logger.info(f"[_record_agent_step] Successfully recorded step {data.get('step_number')} for todo {self.todo_item.id}")
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
        agent = BidReviewAgent(
            project_id=self.todo_item.project_id,
            tender_doc_path=self.tender_doc_path,
            bid_doc_path=self.bid_doc_path,
            user_id=self.user_id,
            rule_doc_path=self.todo_item.rule_doc_path,
            event_callback=self.event_callback,
            logger=logger,
            max_steps=max_steps,
            cancel_event=self.cancel_event,
        )
        # Set task_id for heartbeat tracking (ReviewTask.id, not TodoItem.id)
        agent._task_id = self.session_id
        await agent.initialize()
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
            logger.info(f"[SubAgentExecutor.execute] Findings: {findings}")

            return {
                "success": True,
                "findings": findings,
                "todo_id": self.todo_item.id,
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

    改进：不依赖 check_items 数量，仅检测基本异常。
    """
    if not result:
        return True

    if not result.get("success", False):
        return True

    findings = result.get("findings", [])
    if not findings:
        return True

    return False