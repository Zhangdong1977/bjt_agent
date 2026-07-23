"""Rule-driven master agent for technical bid duplicate checking."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from sqlalchemy import update

from backend.agent.duplicate_check_agent import DuplicateCheckAgent
from backend.models import DuplicateResult, TodoItem
from backend.services.duplicate_candidates import DuplicateCandidateService
from backend.services.todo_service import TodoService
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)


def summarize_sub_agent_results(raw_results: list) -> dict[str, int]:
    """Aggregate concurrent sub-agent outcomes, including raised exceptions."""
    completed = 0
    failed = 0
    finding_count = 0
    for result in raw_results:
        if not isinstance(result, dict) or not result.get("success"):
            failed += 1
        else:
            completed += 1
            finding_count += int(result.get("finding_count", 0))
    return {
        "total": len(raw_results),
        "completed": completed,
        "failed": failed,
        "finding_count": finding_count,
    }


class DuplicateMasterAgent:
    """Scan rule files, create TodoItems, run sub-agents and collect results."""

    def __init__(
        self,
        *,
        project_id: str,
        task_id: str,
        user_id: str,
        rule_library_path: str,
        left_document_id: str,
        right_document_id: str,
        candidate_service: DuplicateCandidateService,
        session_factory,
        max_concurrency: int,
        event_callback: Callable[[str, dict], None] | None = None,
        cancel_event: asyncio.Event | None = None,
        max_retries: int = 1,
    ):
        self.project_id = project_id
        self.task_id = task_id
        self.user_id = user_id
        self.rule_library_path = Path(rule_library_path)
        self.left_document_id = left_document_id
        self.right_document_id = right_document_id
        self.candidate_service = candidate_service
        self.session_factory = session_factory
        self.max_concurrency = max(1, max_concurrency)
        self.event_callback = event_callback
        self.cancel_event = cancel_event or asyncio.Event()
        self.max_retries = max_retries

    def _event(self, event_type: str, data: dict) -> None:
        if self.event_callback:
            self.event_callback(event_type, data)

    async def run(self) -> dict:
        self._event("master_started", {"message": "开始扫描查重规则库"})
        self._event(
            "step",
            {
                "step_number": 1,
                "step_type": "observation",
                "content": "扫描技术应标书查重规则目录",
            },
        )
        if not self.rule_library_path.is_dir():
            return {"success": False, "error": "查重规则目录不存在"}
        rules = sorted(self.rule_library_path.glob("*.md"), key=lambda path: path.name)
        if not rules:
            return {"success": False, "error": "查重规则目录中没有规则文件"}

        self._event(
            "master_scan_completed",
            {"total_docs": len(rules), "rule_docs": [path.name for path in rules]},
        )

        async with self.session_factory() as db:
            todo_service = TodoService(db)
            todos = []
            for rule in rules:
                todo = await todo_service.create_todo(
                    project_id=self.project_id,
                    session_id=self.task_id,
                    rule_doc_path=str(rule),
                    rule_doc_name=rule.name,
                    check_items=None,
                )
                todos.append(todo)
                self._event(
                    "todo_created",
                    {
                        "todo_id": todo.id,
                        "rule_doc_name": todo.rule_doc_name,
                    },
                )

        self._event("todo_list_completed", {"total_todos": len(todos)})
        self._event(
            "step",
            {
                "step_number": 2,
                "step_type": "observation",
                "content": f"已按 {len(todos)} 份规则创建查重子代理",
            },
        )

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def guarded(todo):
            async with semaphore:
                return await self._run_todo(todo)

        raw_results = await asyncio.gather(
            *(guarded(todo) for todo in todos), return_exceptions=True
        )
        summary = summarize_sub_agent_results(raw_results)
        completed = summary["completed"]
        failed = summary["failed"]

        if completed == 0:
            return {
                "success": False,
                "error": "所有查重子代理均失败，未生成有效结果",
                "stats": summary,
            }

        self._event("merging_started", {"message": "汇总查重结果"})
        self._event("merging_completed", {"result": summary})
        if failed:
            self._event(
                "warning",
                {"message": f"{failed} 个查重子代理失败，结果可能不完整", "stats": summary},
            )
        return {"success": True, "stats": summary}

    async def _run_todo(self, todo) -> dict:
        if self.cancel_event.is_set():
            return {"success": False, "error": "Task cancelled"}

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            async with self.session_factory() as db:
                service = TodoService(db)
                await service.update_todo_status(todo.id, "running")
            self._event(
                "sub_agent_started",
                {
                    "todo_id": todo.id,
                    "rule_doc_name": todo.rule_doc_name,
                    "max_steps": 2,
                },
            )
            try:
                if self.cancel_event.is_set():
                    raise asyncio.CancelledError()
                agent = DuplicateCheckAgent(
                    rule_doc_path=todo.rule_doc_path,
                    candidate_service=self.candidate_service,
                    task_id=self.task_id,
                    todo_id=todo.id,
                    project_id=self.project_id,
                    user_id=self.user_id,
                    session_factory=self.session_factory,
                    event_callback=self.event_callback,
                    cancel_event=self.cancel_event,
                )
                findings, check_items = await agent.run()
                if self.cancel_event.is_set():
                    raise asyncio.CancelledError()
                reasonable = sum(item.verdict == "reasonable" for item in findings)
                suspicious = sum(item.verdict == "suspicious" for item in findings)
                async with self.session_factory() as db:
                    for payload in findings:
                        db.add(
                            DuplicateResult(
                                task_id=self.task_id,
                                todo_id=todo.id,
                                rule_doc_name=todo.rule_doc_name,
                                left_document_id=self.left_document_id,
                                right_document_id=self.right_document_id,
                                **payload.model_dump(),
                            )
                        )
                    # Findings and their Todo summary must become visible in one
                    # transaction; otherwise a retry after a partial commit can
                    # insert duplicate result rows.
                    await db.execute(
                        update(TodoItem)
                        .where(TodoItem.id == todo.id)
                        .values(
                            check_items=check_items,
                            status="completed",
                            result={
                                "finding_count": len(findings),
                                "reasonable_count": reasonable,
                                "suspicious_count": suspicious,
                                "findings": [item.model_dump() for item in findings],
                            },
                            brain_capacity=1.0,
                            max_steps=2,
                            completed_at=utc_now(),
                            updated_at=utc_now(),
                        )
                    )
                    await db.commit()
                self._event(
                    "sub_agent_completed",
                    {
                        "todo_id": todo.id,
                        "findings_count": len(findings),
                        "findings": [item.model_dump() for item in findings],
                        "brain_capacity": 1.0,
                    },
                )
                return {"success": True, "finding_count": len(findings)}
            except asyncio.CancelledError:
                async with self.session_factory() as db:
                    await TodoService(db).update_todo_status(
                        todo.id, "failed", error_message="Task cancelled"
                    )
                self._event("sub_agent_failed", {"todo_id": todo.id, "error": "Task cancelled"})
                return {"success": False, "error": "Task cancelled"}
            except Exception as exc:
                last_error = exc
                logger.exception(
                    "Duplicate sub-agent failed: todo=%s attempt=%s", todo.id, attempt + 1
                )
                if attempt < self.max_retries and not self.cancel_event.is_set():
                    async with self.session_factory() as db:
                        await TodoService(db).reset_todo_for_retry(todo.id, attempt + 1)
                    await asyncio.sleep(2 ** attempt)
                    continue
                async with self.session_factory() as db:
                    await TodoService(db).update_todo_status(
                        todo.id,
                        "failed",
                        error_message=str(exc),
                        max_steps=2,
                    )
                self._event(
                    "sub_agent_failed",
                    {"todo_id": todo.id, "error": str(exc), "brain_capacity": 0.0},
                )
        return {"success": False, "error": str(last_error)}
