"""MasterAgent - 主代理 - 解析规则库，生成待办列表，并行启动子代理，汇总结果."""

import json
import logging
from typing import Optional, Callable

from .tools.rule_parser import RuleLibraryScannerTool
from .sub_agent_executor import SubAgentExecutor, detect_anomaly
from backend.services.todo_service import TodoService
from backend.services.task_merge_service import TaskMergeService

logger = logging.getLogger(__name__)


class MasterAgent:
    """主代理 - 解析规则库，生成待办列表，并行启动子代理，汇总结果."""

    def __init__(
        self,
        project_id: str,
        rule_library_path: str,
        tender_doc_path: str,
        bid_doc_path: str,
        user_id: str,
        event_callback: Optional[Callable] = None,
        max_retries: int = 3,
    ):
        self.project_id = project_id
        self.rule_library_path = rule_library_path
        self.tender_doc_path = tender_doc_path
        self.bid_doc_path = bid_doc_path
        self.user_id = user_id
        self.event_callback = event_callback
        self.max_retries = max_retries

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

    async def run(self, todo_service, session_id: str, session_factory=None) -> dict:
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
        """
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

        # Phase 3: 串行执行子代理
        await self._run_sub_agents(todo_service)

        # Phase 4: 汇总结果
        self._send_event("merging_started", {"message": "开始合并结果"})
        merged_result = await self._merge_results(todo_service)
        self._send_event("merging_completed", {"result": merged_result})

        return {
            "success": True,
            "session_id": session_id,
            "total_todos": len(self._todo_items),
            "merged_result": merged_result,
        }

    async def _run_sub_agents(self, todo_service) -> None:
        """串行执行所有子代理."""
        logger.info(f"[_run_sub_agents] Starting sequential execution with {len(self._todo_items)} todos")
        for i, todo in enumerate(self._todo_items):
            try:
                result = await self._run_single_sub_agent(todo, todo_service, self._session_factory)
                logger.info(f"[_run_sub_agents] Task {i+1}/{len(self._todo_items)} completed, success={result.get('success')}")
            except Exception as e:
                logger.error(f"[_run_sub_agents] Task {i+1} raised exception: {e}")
        logger.info(f"[_run_sub_agents] All tasks completed")

    async def _run_single_sub_agent(self, todo, todo_service, session_factory=None) -> dict:
        """执行单个子代理.

        Args:
            todo: TodoItem to process
            todo_service: TodoService for sequential operations (phase 2 todo creation)
            session_factory: Optional session factory. If provided, each retry creates
                          a fresh TodoService with its own database session.
        """
        logger.info(f"[_run_single_sub_agent] Starting for todo_id={todo.id}, rule_doc_name={todo.rule_doc_name}")
        retry_count = 0

        while retry_count <= self.max_retries:
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

                # 执行子代理
                executor = SubAgentExecutor(
                    todo_item=todo,
                    tender_doc_path=self.tender_doc_path,
                    bid_doc_path=self.bid_doc_path,
                    user_id=self.user_id,
                    event_callback=self._create_sub_agent_callback(todo.id),
                )
                logger.info(f"[_run_single_sub_agent] Calling executor.execute() for todo {todo.id}")
                result = await executor.execute()
                error_msg = result.get('error', '')
                logger.info(f"[_run_single_sub_agent] executor.execute() returned for todo {todo.id}, success={result.get('success')}, error={error_msg}, findings_count={len(result.get('findings', []))}")

                # 检测异常
                if detect_anomaly(result, todo):
                    logger.info(f"[_run_single_sub_agent] Anomaly detected for todo {todo.id}, retry_count={retry_count}")
                    if retry_count < self.max_retries:
                        retry_count += 1
                        await task_todo_service.reset_todo_for_retry(todo.id, retry_count)
                        continue
                    else:
                        await task_todo_service.update_todo_status(
                            todo.id, "failed", error_message="Max retries exceeded"
                        )
                        self._send_event("sub_agent_failed", {
                            "todo_id": todo.id,
                            "error": "Max retries exceeded",
                        })
                        return result

                # 成功
                findings = result.get("findings", [])
                await task_todo_service.update_todo_status(
                    todo.id, "completed", result={"findings": findings}
                )
                await task_todo_service.increment_completed_todos(self._session_id)

                self._send_event("sub_agent_completed", {
                    "todo_id": todo.id,
                    "findings_count": len(findings),
                    "findings": findings,
                })

                return result

            except Exception as e:
                logger.error(f"[_run_single_sub_agent] Exception for todo {todo.id}: {e}")
                if retry_count < self.max_retries:
                    retry_count += 1
                    await task_todo_service.reset_todo_for_retry(todo.id, retry_count)
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

    async def _merge_results(self, todo_service) -> dict:
        """合并所有子代理结果.

        Uses TaskMergeService with LLM to intelligently merge findings from sub-agents.
        Uses a fresh session to ensure we can see all sub-agent committed updates.
        """
        # Create a fresh session to query todos with their updated results
        merge_session = None
        merge_todo_service = todo_service
        if self._session_factory is not None:
            merge_session = self._session_factory()
            merge_todo_service = TodoService(merge_session)

        try:
            todos = await merge_todo_service.get_session_todos(self._session_id)

            # Collect all findings from all sub-agents
            all_findings = []
            for todo in todos:
                if todo.result and "findings" in todo.result:
                    for finding in todo.result["findings"]:
                        all_findings.append(finding)

            logger.info(f"[_merge_results] Collected {len(all_findings)} findings from {len(todos)} sub-agents")

            # Use LLM-based merge service
            agent = None
            try:
                # Import here to avoid circular imports
                from backend.agent.bid_review_agent import BidReviewAgent

                # Create BidReviewAgent for merge decisions (paths don't matter since we only use MergeDeciderTool)
                agent = BidReviewAgent(
                    project_id=self.project_id,
                    tender_doc_path="",
                    bid_doc_path="",
                    user_id=self.user_id,
                    rule_doc_path="",
                    event_callback=None,
                    max_steps=1,
                )
                await agent.initialize()

                task_merge_service = TaskMergeService(agent)
                result = await task_merge_service.merge_sub_agent_results(
                    findings=all_findings,
                    event_callback=self._send_event,
                )
                logger.info(f"[_merge_results] TaskMergeService returned: total={result['total_findings']}, critical={result['critical_count']}, major={result['major_count']}, minor={result['minor_count']}, passed={result['passed_count']}")
            finally:
                if agent is not None:
                    await agent.close()

            return result
        finally:
            if merge_session is not None:
                await merge_session.close()
