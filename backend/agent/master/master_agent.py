"""MasterAgent - 主代理 - 解析规则库，生成待办列表，并行启动子代理，汇总结果."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, Callable

from sqlalchemy import update

from .tools.rule_parser import RuleLibraryScannerTool
from .sub_agent_executor import SubAgentExecutor, detect_anomaly
from backend.services.todo_service import TodoService
from backend.models.review_task import ReviewTask
from backend.config import get_settings

logger = logging.getLogger(__name__)


class MasterAgent:
    """主代理 - 解析规则库，生成待办列表，并行启动子代理，汇总结果."""

    def __init__(
        self,
        project_id: str,
        rule_library_path: str,
        tender_docs: list[tuple[str, str]],
        bid_docs: list[tuple[str, str]],
        user_id: str,
        event_callback: Optional[Callable] = None,
        max_retries: int = 3,
        cancel_event: Optional[asyncio.Event] = None,
        on_sub_agent_result: Optional[Callable] = None,
        max_concurrency: Optional[int] = None,
    ):
        self.project_id = project_id
        self.rule_library_path = rule_library_path
        self.tender_docs = tender_docs
        self.bid_docs = bid_docs
        self.user_id = user_id
        self.event_callback = event_callback
        self.max_retries = max_retries
        self.cancel_event = cancel_event
        self.on_sub_agent_result = on_sub_agent_result
        self._max_concurrency = max_concurrency or get_settings().max_sub_agent_concurrency

        self.scanner = RuleLibraryScannerTool()
        self._todo_items = []
        self._session_id: Optional[str] = None

    def _send_event(self, event_type: str, data: dict):
        """发送 SSE 事件."""
        if self.event_callback:
            try:
                self.event_callback(event_type, data)
            except Exception as e:
                logger.warning(f"[MasterAgent._send_event] Event callback failed: event_type={event_type}, error={e}")

    async def run(self, todo_service, session_id: str, session_factory=None, cancel_event: Optional[asyncio.Event] = None) -> dict:
        """
        执行主代理工作流程.

        1. 扫描规则库
        2. 创建待办任务（直接传递 rule_doc_path）
        3. 并行执行子代理
        4. 汇总结果

        Args:
            todo_service: TodoService instance for sequential Phase 2 operations
            session_id: Review session ID
            session_factory: Optional async session factory for parallel sub-agent tasks.
                          If provided, each parallel task will create its own session.
            cancel_event: Optional asyncio.Event to signal cancellation from heartbeat timeout.
        """
        # Use instance cancel_event if not provided
        if cancel_event is None:
            cancel_event = self.cancel_event

        self._session_id = session_id
        self._session_factory = session_factory  # Store for parallel tasks
        self._send_event("master_started", {"message": "开始解析规则库"})

        # Phase 1: 扫描规则库
        scan_result = await self.scanner.execute(self.rule_library_path)
        if not scan_result.success:
            return {"success": False, "error": scan_result.error}

        rule_docs = json.loads(scan_result.content)["rule_docs"]
        self._send_event("master_scan_completed", {
            "total_docs": len(rule_docs),
            "rule_docs": [d["name"] for d in rule_docs],
        })

        # Phase 2: 创建待办（不再解析规则文档）
        for doc in rule_docs:
            # 创建 todo item，直接传递 rule_doc_path
            todo = await todo_service.create_todo(
                project_id=self.project_id,
                session_id=session_id,
                rule_doc_path=doc["path"],
                rule_doc_name=doc["name"],
                check_items=None,  # 不再解析 check_items
            )
            self._todo_items.append(todo)

            self._send_event("todo_created", {
                "todo_id": todo.id,
                "rule_doc_name": doc["name"],
                "rule_doc_path": doc["path"],
            })

        self._send_event("todo_list_completed", {
            "total_todos": len(self._todo_items),
        })

        # Phase 3: 并行执行子代理（受信号量控制）
        await self._run_sub_agents(todo_service, cancel_event)

        # Skip merge phase if cancelled
        if cancel_event and cancel_event.is_set():
            logger.warning("[MasterAgent.run] Cancelled, skipping merge phase")
            return {"success": False, "error": "Task cancelled"}

        # Phase 4: 简单汇总统计
        self._send_event("merging_started", {"message": "汇总审查结果"})
        merged_result = await self._simple_aggregate(todo_service)
        self._send_event("merging_completed", {"result": merged_result})

        return {
            "success": True,
            "session_id": session_id,
            "total_todos": len(self._todo_items),
            "merged_result": merged_result,
        }

    async def _run_sub_agents(self, todo_service, cancel_event: Optional[asyncio.Event] = None) -> None:
        """并行执行所有子代理（受信号量控制并发数）."""
        max_concurrency = self._max_concurrency
        total = len(self._todo_items)
        logger.info(
            f"[_run_sub_agents] Starting parallel execution with {total} todos, "
            f"max_concurrency={max_concurrency}"
        )

        semaphore = asyncio.Semaphore(max_concurrency)

        async def _run_with_semaphore(todo):
            async with semaphore:
                if cancel_event and cancel_event.is_set():
                    return {"success": False, "error": "Cancelled before start"}
                try:
                    return await self._run_single_sub_agent(todo, todo_service, self._session_factory, cancel_event)
                except asyncio.CancelledError:
                    return {"success": False, "error": "Task cancelled"}

        tasks = [_run_with_semaphore(todo) for todo in self._todo_items]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.error(f"[_run_sub_agents] Task {i+1}/{total} raised {type(r).__name__}: {r}")
            else:
                logger.info(f"[_run_sub_agents] Task {i+1}/{total} completed, success={r.get('success')}")
        logger.info(f"[_run_sub_agents] All {total} tasks completed")

    @staticmethod
    def _cancel_error_message(result: dict | None = None) -> str:
        """Get cancellation error message based on the reason."""
        cancel_reason = (result or {}).get("_cancel_reason") if result else None
        if cancel_reason == "heartbeat_timeout":
            return "Task cancelled: heartbeat timeout (no progress detected)"
        return "Task cancelled by user"

    async def _run_single_sub_agent(self, todo, todo_service, session_factory=None, cancel_event: Optional[asyncio.Event] = None) -> dict:
        """执行单个子代理.

        Args:
            todo: TodoItem to process
            todo_service: TodoService for sequential operations (phase 2 todo creation)
            session_factory: Optional session factory. If provided, each retry creates
                          a fresh TodoService with its own database session.
            cancel_event: Optional asyncio.Event to signal cancellation. If set, the
                         sub-agent will stop retrying and return cancelled.
        """
        logger.info(f"[_run_single_sub_agent] Starting for todo_id={todo.id}, rule_doc_name={todo.rule_doc_name}")
        retry_count = 0

        while retry_count <= self.max_retries:
            # Check parent cancel_event before each attempt — respect API cancellation
            if cancel_event and cancel_event.is_set():
                logger.warning(f"[_run_single_sub_agent] Parent cancel_event set, stopping for todo_id={todo.id}")
                err_msg = self._cancel_error_message()
                await task_todo_service.update_todo_status(todo.id, "failed", error_message=err_msg)
                self._send_event("sub_agent_failed", {
                    "todo_id": todo.id,
                    "error": err_msg,
                })
                return {"success": False, "error": err_msg}

            # Create a fresh TodoService for each retry iteration if factory is available
            task_todo_service = todo_service
            session_to_close = None
            if session_factory is not None:
                task_session = session_factory()
                task_todo_service = TodoService(task_session)
                session_to_close = task_session

            try:
                # 更新状态为 running
                logger.info(f"[_run_single_sub_agent] Updating todo {todo.id} status to running")
                await task_todo_service.update_todo_status(todo.id, "running")

                # 执行子代理 — 直接传递父级 cancel_event 使所有子代理共享取消信号
                executor = SubAgentExecutor(
                    todo_item=todo,
                    tender_docs=self.tender_docs,
                    bid_docs=self.bid_docs,
                    user_id=self.user_id,
                    session_factory=session_factory,
                    event_callback=self._create_sub_agent_callback(todo.id),
                    session_id=self._session_id,
                    cancel_event=cancel_event,
                )
                logger.info(f"[_run_single_sub_agent] Calling executor.execute() for todo {todo.id}")
                result = await executor.execute()
                error_msg = result.get('error', '')
                logger.info(f"[_run_single_sub_agent] executor.execute() returned for todo {todo.id}, success={result.get('success')}, error={error_msg}, findings_count={len(result.get('findings', []))}")

                # 检查取消状态 — execute() 返回后（可能被 heartbeat monitor 取消）
                if cancel_event and cancel_event.is_set():
                    logger.warning(f"[_run_single_sub_agent] Cancelled after execute for todo_id={todo.id}")
                    err_msg = self._cancel_error_message(result)
                    brain_cap = result.get('brain_capacity', 0.0)
                    await task_todo_service.update_todo_status(
                        todo.id, "failed", error_message=err_msg,
                        brain_capacity=brain_cap, max_steps=100,
                    )
                    self._send_event("sub_agent_failed", {"todo_id": todo.id, "error": err_msg, "brain_capacity": brain_cap})
                    return {"success": False, "error": err_msg}

                # Max steps exceeded — fail fast, no retry (retrying would hit the same limit)
                is_max_steps = result.get("error", "").startswith("Max steps exceeded")
                if is_max_steps:
                    brain_cap = result.get('brain_capacity', 0.0)
                    await task_todo_service.update_todo_status(
                        todo.id, "failed", error_message=result["error"],
                        brain_capacity=brain_cap, max_steps=100,
                    )
                    self._send_event("sub_agent_failed", {
                        "todo_id": todo.id,
                        "error": result["error"],
                        "brain_capacity": brain_cap,
                    })
                    return result

                # 检测异常
                if detect_anomaly(result, todo):
                    logger.info(f"[_run_single_sub_agent] Anomaly detected for todo {todo.id}, retry_count={retry_count}")
                    if retry_count < self.max_retries:
                        retry_count += 1
                        # Check cancel_event before retrying
                        if cancel_event and cancel_event.is_set():
                            logger.warning(f"[_run_single_sub_agent] Cancel requested during anomaly retry, stopping for todo_id={todo.id}")
                            err_msg = self._cancel_error_message(result)
                            brain_cap = result.get('brain_capacity', 0.0)
                            await task_todo_service.update_todo_status(
                                todo.id, "failed", error_message=err_msg,
                                brain_capacity=brain_cap, max_steps=100,
                            )
                            self._send_event("sub_agent_failed", {"todo_id": todo.id, "error": err_msg, "brain_capacity": brain_cap})
                            return {"success": False, "error": err_msg}
                        await task_todo_service.reset_todo_for_retry(todo.id, retry_count)
                        # Exponential backoff: 2s, 4s, 8s, ...
                        backoff = 2 ** retry_count
                        logger.info(f"[_run_single_sub_agent] Backing off {backoff}s before retry {retry_count} for todo {todo.id}")
                        await asyncio.sleep(backoff)
                        await self._refresh_heartbeat(session_factory)
                        continue
                    else:
                        brain_cap = result.get('brain_capacity', 0.0)
                        await task_todo_service.update_todo_status(
                            todo.id, "failed", error_message="Max retries exceeded",
                            brain_capacity=brain_cap, max_steps=100,
                        )
                        self._send_event("sub_agent_failed", {
                            "todo_id": todo.id,
                            "error": "Max retries exceeded",
                            "brain_capacity": brain_cap,
                        })
                        return result

                # 成功
                findings = result.get("findings", [])
                brain_cap = result.get('brain_capacity', 0.0)
                report_path = result.get("report_path")
                result_data = {"findings": findings}
                if report_path:
                    result_data["report_path"] = report_path
                await task_todo_service.update_todo_status(
                    todo.id, "completed", result=result_data,
                    brain_capacity=brain_cap, max_steps=100,
                )
                # 持久化检查项列表，用于统计检查项总数
                check_items = result.get("check_items", [])
                if check_items:
                    await task_todo_service.update_todo_check_items(todo.id, check_items)
                await task_todo_service.increment_completed_todos(self._session_id)

                self._send_event("sub_agent_completed", {
                    "todo_id": todo.id,
                    "findings_count": len(findings),
                    "findings": findings,
                    "brain_capacity": brain_cap,
                })

                # Incremental save callback for ReviewResult persistence
                if self.on_sub_agent_result and findings:
                    try:
                        await self.on_sub_agent_result(findings)
                    except Exception as e:
                        logger.warning(f"[_run_single_sub_agent] on_sub_agent_result callback failed: {e}")

                return result

            except asyncio.CancelledError:
                # Propagate cancellation upward — do not retry
                logger.warning(f"[_run_single_sub_agent] CancelledError for todo {todo.id}, retry_count={retry_count}")
                if session_to_close:
                    await session_to_close.close()
                raise

            except Exception as e:
                logger.error(f"[_run_single_sub_agent] Exception for todo {todo.id}: {e}")
                if retry_count < self.max_retries:
                    retry_count += 1
                    # Check cancel_event before retrying
                    if cancel_event and cancel_event.is_set():
                        logger.warning(f"[_run_single_sub_agent] Cancel requested during exception retry, stopping for todo_id={todo.id}")
                        err_msg = self._cancel_error_message()
                        await task_todo_service.update_todo_status(todo.id, "failed", error_message=err_msg)
                        self._send_event("sub_agent_failed", {"todo_id": todo.id, "error": err_msg})
                        return {"success": False, "error": err_msg}
                    await task_todo_service.reset_todo_for_retry(todo.id, retry_count)
                    # Exponential backoff: 2s, 4s, 8s, ...
                    backoff = 2 ** retry_count
                    logger.info(f"[_run_single_sub_agent] Backing off {backoff}s before retry {retry_count} for todo {todo.id}")
                    await asyncio.sleep(backoff)
                    await self._refresh_heartbeat(session_factory)
                else:
                    await task_todo_service.update_todo_status(
                        todo.id, "failed", error_message=str(e)
                    )
                    self._send_event("sub_agent_failed", {
                        "todo_id": todo.id,
                        "error": str(e),
                    })
                    return {"success": False, "error": str(e)}
            finally:
                # Clean up the session if we created one
                if session_to_close is not None:
                    await session_to_close.close()

    def _create_sub_agent_callback(self, todo_id: str) -> Optional[Callable]:
        """创建子代理的 event callback 包装."""
        def callback(event_type: str, data: dict):
            data["todo_id"] = todo_id
            # Avoid double prefix: BidReviewAgent already emits events with sub_agent_ prefix
            # e.g., sub_agent_step, sub_agent_completed, completed
            if event_type.startswith("sub_agent_"):
                self._send_event(event_type, data)
            else:
                self._send_event(f"sub_agent_{event_type}", data)
        return callback

    async def _refresh_heartbeat(self, session_factory) -> None:
        """Refresh ReviewTask.last_heartbeat to reset the heartbeat timeout window.

        Called before each retry to prevent the new sub-agent from being
        immediately cancelled by a stale heartbeat timestamp.
        """
        if not self._session_id or not session_factory:
            return
        try:
            async with session_factory() as db:
                await db.execute(
                    update(ReviewTask)
                    .where(ReviewTask.id == self._session_id)
                    .values(last_heartbeat=datetime.utcnow())
                )
                await db.commit()
            logger.info(f"[_refresh_heartbeat] Refreshed heartbeat for session {self._session_id}")
        except Exception as e:
            logger.warning(f"[_refresh_heartbeat] Failed to refresh heartbeat: {e}")

    async def _simple_aggregate(self, todo_service) -> dict:
        """简单汇总所有子代理结果，不做 LLM 合并.

        Collects findings from all completed sub-agents and computes statistics.
        """
        merge_session = None
        merge_todo_service = todo_service
        if self._session_factory is not None:
            merge_session = self._session_factory()
            merge_todo_service = TodoService(merge_session)

        try:
            todos = await merge_todo_service.get_session_todos(self._session_id)

            all_findings = []
            for todo in todos:
                if todo.result and "findings" in todo.result:
                    all_findings.extend(todo.result["findings"])

            critical = sum(1 for f in all_findings if not f.get("is_compliant") and f.get("severity") == "critical")
            major = sum(1 for f in all_findings if not f.get("is_compliant") and f.get("severity") == "major")
            minor = sum(1 for f in all_findings if not f.get("is_compliant") and f.get("severity") not in ("critical", "major"))
            passed = sum(1 for f in all_findings if f.get("is_compliant"))

            logger.info(
                f"[_simple_aggregate] {len(all_findings)} findings from {len(todos)} sub-agents: "
                f"critical={critical}, major={major}, minor={minor}, passed={passed}"
            )

            return {
                "total_findings": len(all_findings),
                "critical_count": critical,
                "major_count": major,
                "minor_count": minor,
                "passed_count": passed,
                "findings": all_findings,
            }
        finally:
            if merge_session is not None:
                await merge_session.close()
