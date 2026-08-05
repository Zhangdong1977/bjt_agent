"""Rule-driven master agent for technical bid duplicate checking."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from sqlalchemy import update

from backend.agent.duplicate_check_agent import DuplicateCheckAgent
from backend.models import DuplicateOccurrence, DuplicateResult, TodoItem
from backend.services.duplicate_candidates import DuplicateCandidateService
from backend.services.duplicate_rules import RuleValidationError, load_duplicate_rules
from backend.services.duplicate_sources import DuplicateSourceIndex
from backend.services.duplicate_result_grouper import group_duplicate_findings
from backend.services.todo_service import TodoService
from backend.utils.fs_encoding import decode_fs_name, heal_directory
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
        source_index: DuplicateSourceIndex | None = None,
        session_factory,
        max_concurrency: int,
        event_callback: Callable[[str, dict], None] | None = None,
        cancel_event: asyncio.Event | None = None,
        max_retries: int = 1,
        coverage_status: str = "insufficient",
    ):
        self.project_id = project_id
        self.task_id = task_id
        self.user_id = user_id
        self.rule_library_path = Path(rule_library_path)
        self.left_document_id = left_document_id
        self.right_document_id = right_document_id
        self.candidate_service = candidate_service
        self.source_index = source_index
        self.session_factory = session_factory
        self.max_concurrency = max(1, max_concurrency)
        self.event_callback = event_callback
        self.cancel_event = cancel_event or asyncio.Event()
        self.max_retries = max_retries
        self.coverage_status = (
            coverage_status
            if coverage_status in {"complete", "partial", "insufficient"}
            else "insufficient"
        )

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
                "content": "扫描技术投标文件查重规则目录",
            },
        )
        if not self.rule_library_path.is_dir():
            return {"success": False, "error": "查重规则目录不存在"}
        # Self-heal non-UTF-8 (e.g. GBK) rule filenames before loading: a
        # Windows deploy can leave GBK filename bytes on disk, which pathlib
        # reads as surrogateescape strings that the name validation in
        # load_duplicate_rule and asyncpg cannot handle.
        heal_directory(self.rule_library_path)
        try:
            rule_specs = load_duplicate_rules(self.rule_library_path)
        except RuleValidationError as exc:
            logger.error("Duplicate rule library validation failed: %s", exc)
            self._event("error", {"message": "查重规则库校验失败", "detail": str(exc)})
            return {"success": False, "error": f"查重规则库校验失败：{exc}"}
        rules = sorted(self.rule_library_path.glob("*.md"), key=lambda path: decode_fs_name(path.name))

        self._event(
            "master_scan_completed",
            {
                "total_docs": len(rules),
                "rule_docs": [decode_fs_name(path.name) for path in rules],
                "rule_versions": {
                    rule.rule_id: rule.version for rule in rule_specs
                },
            },
        )

        async with self.session_factory() as db:
            todo_service = TodoService(db)
            todos = []
            for rule in rules:
                todo = await todo_service.create_todo(
                    project_id=self.project_id,
                    session_id=self.task_id,
                    rule_doc_path=decode_fs_name(str(rule)),
                    rule_doc_name=decode_fs_name(rule.name),
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
                    source_index=self.source_index,
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
                raw_finding_count = len(findings)
                findings = group_duplicate_findings(findings)
                reasonable = sum(item.verdict == "reasonable" for item in findings)
                suspicious = sum(item.verdict == "suspicious" for item in findings)
                unknown = sum(item.verdict == "unknown" for item in findings)
                async with self.session_factory() as db:
                    for payload in findings:
                        payload_data = payload.model_dump()
                        evidence = payload_data.get("evidence") or {}
                        left_document_id = str(
                            evidence.get("left_document_id") or self.left_document_id
                        )
                        right_document_id = str(
                            evidence.get("right_document_id") or self.right_document_id
                        )
                        channel_scores = {
                            key: evidence.get(key)
                            for key in (
                                "lexical_score",
                                "semantic_score",
                                "structure_score",
                                "image_score",
                            )
                            if evidence.get(key) is not None
                        }
                        confidence = evidence.get("evidence_strength")
                        if confidence is not None:
                            try:
                                confidence = min(1.0, max(0.0, float(confidence)))
                            except (TypeError, ValueError):
                                confidence = None
                        for key in ("confidence", "coverage_status", "channel_scores"):
                            payload_data.pop(key, None)
                        finding_row = DuplicateResult(
                                task_id=self.task_id,
                                todo_id=todo.id,
                                rule_doc_name=todo.rule_doc_name,
                                left_document_id=left_document_id,
                                right_document_id=right_document_id,
                                confidence=confidence,
                                coverage_status=self.coverage_status,
                                channel_scores=channel_scores or None,
                                **payload_data,
                            )
                        db.add(finding_row)
                        await db.flush()
                        channel = (
                            "image"
                            if payload.match_type in {"ocr_error"}
                            or (evidence.get("image_score") or 0) > 0
                            else (
                                "semantic"
                                if payload.match_type == "semantic"
                                else (
                                    "structure"
                                    if payload.match_type == "structural"
                                    else "lexical"
                                )
                            )
                        )
                        occurrence_payloads = evidence.get("occurrences")
                        if not isinstance(occurrence_payloads, list) or not occurrence_payloads:
                            occurrence_payloads = [
                                {
                                    "left_document_id": left_document_id,
                                    "right_document_id": right_document_id,
                                    "left_block_id": evidence.get("left_block_id"),
                                    "right_block_id": evidence.get("right_block_id"),
                                    "left_excerpt": payload.left_excerpt,
                                    "right_excerpt": payload.right_excerpt,
                                    "left_location": payload.left_location,
                                    "right_location": payload.right_location,
                                }
                            ]
                        occurrence_rows = []
                        seen_occurrences: set[tuple] = set()
                        for occurrence in occurrence_payloads:
                            if not isinstance(occurrence, dict):
                                continue
                            for side, fallback_document_id, fallback_excerpt, fallback_location in (
                                (
                                    "left",
                                    left_document_id,
                                    payload.left_excerpt,
                                    payload.left_location,
                                ),
                                (
                                    "right",
                                    right_document_id,
                                    payload.right_excerpt,
                                    payload.right_location,
                                ),
                            ):
                                document_id = str(
                                    occurrence.get(f"{side}_document_id")
                                    or fallback_document_id
                                )
                                block_id = occurrence.get(f"{side}_block_id")
                                excerpt = str(
                                    occurrence.get(f"{side}_excerpt")
                                    or fallback_excerpt
                                )
                                location = occurrence.get(f"{side}_location")
                                if not isinstance(location, dict):
                                    location = fallback_location
                                occurrence_key = (
                                    document_id,
                                    block_id,
                                    excerpt,
                                    str(sorted(location.items())),
                                )
                                if occurrence_key in seen_occurrences:
                                    continue
                                seen_occurrences.add(occurrence_key)
                                occurrence_rows.append(
                                    DuplicateOccurrence(
                                        task_id=self.task_id,
                                        finding_id=finding_row.id,
                                        document_id=document_id,
                                        block_id=block_id,
                                        excerpt=excerpt,
                                        location=location,
                                        channel=channel,
                                    )
                                )
                        db.add_all(occurrence_rows)
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
                                "raw_finding_count": raw_finding_count,
                                "reasonable_count": reasonable,
                                "suspicious_count": suspicious,
                                "unknown_count": unknown,
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
                        "raw_findings_count": raw_finding_count,
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
