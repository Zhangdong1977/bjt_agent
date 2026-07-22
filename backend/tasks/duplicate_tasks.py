"""Celery orchestration for duplicate-check document-pair agents."""

from __future__ import annotations

import asyncio
import logging
from itertools import combinations

from sqlalchemy import func, select, update

from backend.agent.duplicate_check_agent import DuplicateCheckAgent, write_duplicate_report
from backend.celery_app import celery_app
from backend.config import get_settings
from backend.models import Document, DuplicatePairResult, Project, ReviewTask, TodoItem, User
from backend.services.duplicate_rule_loader import load_duplicate_rule
from backend.services.usage_context import UsageContext, reset_usage_context, set_usage_context
from backend.tasks.review_tasks import (
    _publish_event, clear_task_cancelled, create_session_factory,
    is_task_cancelled, run_async,
)
from backend.utils.time_utils import utc_now, utc_seconds_between

logger = logging.getLogger(__name__)


def build_document_pairs(document_ids: list[str]) -> list[tuple[str, str]]:
    """Return stable, unique unordered pairs."""
    return list(combinations(sorted(set(document_ids)), 2))


async def _run_duplicate(task_id: str) -> dict:
    session_factory, engine = create_session_factory()
    settings = get_settings()
    try:
        async with session_factory() as db:
            task = (await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))).scalar_one_or_none()
            if not task or task.task_type != "duplicate":
                return {"status": "error", "message": "查重任务不存在"}
            if task.status in ("completed", "completed_with_warnings", "failed", "cancelled"):
                return {"status": task.status, "message": "任务已处于终态"}
            project = (await db.execute(select(Project).where(Project.id == task.project_id))).scalar_one_or_none()
            if not project:
                task.status, task.error_message = "failed", "项目不存在"
                await db.commit()
                return {"status": "error", "message": task.error_message}
            docs = list((await db.execute(
                select(Document).where(
                    Document.project_id == project.id,
                    Document.doc_type == "duplicate_bid",
                    Document.status == "parsed",
                ).order_by(Document.id)
            )).scalars().all())
            doc_map = {d.id: d for d in docs}
            retry_pairs = (task.config_snapshot or {}).get("retry_document_pairs")
            pairs = [tuple(pair) for pair in retry_pairs] if retry_pairs else build_document_pairs(list(doc_map))
            if not pairs:
                task.status, task.error_message = "failed", "没有可执行的文档对"
                await db.commit()
                return {"status": "error", "message": task.error_message}

            rule_info = (task.config_snapshot or {}).get("rule", {})
            rule_path = rule_info.get("snapshot_path")
            if not rule_path:
                raise ValueError("查重任务缺少规则快照")
            rule = load_duplicate_rule(rule_path)
            expected_rule_hash = rule_info.get("sha256")
            if expected_rule_hash and rule.sha256 != expected_rule_hash:
                raise ValueError("查重规则快照完整性校验失败")
            task.status = "running"
            task.started_at = task.started_at or utc_now()

            todos = list((await db.execute(select(TodoItem).where(
                TodoItem.session_id == task.id, TodoItem.todo_type == "duplicate_pair"
            ).order_by(TodoItem.created_at))).scalars().all())
            if not todos:
                for doc_a_id, doc_b_id in pairs:
                    doc_a, doc_b = doc_map.get(doc_a_id), doc_map.get(doc_b_id)
                    if not doc_a or not doc_b:
                        continue
                    def structure_is_reliable(doc: Document) -> bool:
                        analysis = doc.structure_analysis or {}
                        enough = int(analysis.get("real_heading_count") or 0) >= rule.config.structure.min_real_headings
                        levels_ok = not rule.config.structure.require_multiple_levels or int(analysis.get("max_level") or 0) > 1
                        return doc.structure_quality == "reliable" and enough and levels_ok

                    mode = "structured" if structure_is_reliable(doc_a) and structure_is_reliable(doc_b) else "retrieval"
                    todo = TodoItem(
                        project_id=project.id, session_id=task.id,
                        rule_doc_path=rule_path, rule_doc_name=rule.name,
                        todo_type="duplicate_pair",
                        display_name=f"{doc_a.original_filename} ↔ {doc_b.original_filename}",
                        document_a_id=doc_a.id, document_b_id=doc_b.id,
                        execution_mode=mode, status="pending",
                    )
                    db.add(todo)
                    todos.append(todo)
            await db.commit()
            user_id, project_id = project.user_id, project.id
            concurrency = max(1, task.max_concurrency)

        _publish_event(task_id, "duplicate_master_started", {"total_pairs": len(todos)})
        for todo in todos:
            _publish_event(task_id, "duplicate_pair_created", {
                "todo_id": todo.id, "display_name": todo.display_name,
                "execution_mode": todo.execution_mode,
            })

        semaphore = asyncio.Semaphore(concurrency)

        async def run_one(todo: TodoItem) -> bool:
            async with semaphore:
                if todo.status == "completed":
                    return True
                if is_task_cancelled(task_id):
                    return False
                async with session_factory() as db:
                    current = (await db.execute(select(TodoItem).where(TodoItem.id == todo.id))).scalar_one()
                    current.status, current.started_at = "running", utc_now()
                    await db.commit()
                _publish_event(task_id, "duplicate_pair_started", {
                    "todo_id": todo.id, "display_name": todo.display_name,
                    "execution_mode": todo.execution_mode,
                })

                usage_token = None
                try:
                    async with session_factory() as db:
                        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
                    usage_token = set_usage_context(UsageContext(
                        external_user_id=user.external_user_id if user else None,
                        local_user_id=user_id, user_name=(user.username if user else user_id),
                        enterprise_name=user.enterprise_name if user else None,
                        interior_user=bool(user.interior_user) if user else False,
                        project_id=project_id, task_id=task_id, todo_id=todo.id,
                    ))
                    doc_a, doc_b = doc_map[todo.document_a_id], doc_map[todo.document_b_id]

                    def event_cb(event_type: str, data: dict):
                        payload = {"todo_id": todo.id, "display_name": todo.display_name, **data}
                        _publish_event(task_id, event_type, payload, session_factory=session_factory)
                        if event_type == "duplicate_pair_step":
                            _publish_event(task_id, "sub_agent_step", {
                                "todo_id": todo.id, "step_number": data.get("step_number"),
                                "step_type": "thought", "content": data.get("message", ""),
                            }, session_factory=session_factory)

                    agent = DuplicateCheckAgent(
                        document_a={"id": doc_a.id, "name": doc_a.original_filename,
                                    "parsed_path": doc_a.parsed_markdown_path,
                                    "structure_index_path": doc_a.structure_index_path},
                        document_b={"id": doc_b.id, "name": doc_b.original_filename,
                                    "parsed_path": doc_b.parsed_markdown_path,
                                    "structure_index_path": doc_b.structure_index_path},
                        rule=rule, execution_mode=todo.execution_mode, event_callback=event_cb,
                    )
                    last_error = None
                    for attempt in range(1, 4):
                        try:
                            result, diagnostics = await agent.run()
                            break
                        except Exception as exc:
                            last_error = exc
                            if attempt >= 3 or is_task_cancelled(task_id):
                                raise
                            async with session_factory() as db:
                                current = (await db.execute(select(TodoItem).where(TodoItem.id == todo.id))).scalar_one()
                                current.retry_count = attempt
                                current.error_message = str(exc)[:2000]
                                await db.commit()
                            _publish_event(task_id, "duplicate_pair_step", {
                                "todo_id": todo.id, "display_name": todo.display_name,
                                "step_number": 1000 + attempt,
                                "message": f"子代理执行异常，正在进行第 {attempt + 1} 次尝试",
                            }, session_factory=session_factory)
                            await asyncio.sleep(min(2 ** attempt, 5))
                    else:
                        raise last_error or RuntimeError("查重子代理执行失败")
                    report_dir = settings.workspace_path / user_id / project_id / "duplicate_reports"
                    report_dir.mkdir(parents=True, exist_ok=True)
                    report_path = report_dir / f"pair_{todo.id}.md"
                    write_duplicate_report(report_path, doc_a=doc_a.original_filename,
                                           doc_b=doc_b.original_filename, result=result)
                    result_dict = result.model_dump()
                    async with session_factory() as db:
                        current = (await db.execute(select(TodoItem).where(TodoItem.id == todo.id))).scalar_one()
                        current.status, current.completed_at, current.result = "completed", utc_now(), result_dict
                        db.add(DuplicatePairResult(
                            task_id=task_id, todo_id=todo.id,
                            document_a_id=doc_a.id, document_b_id=doc_b.id,
                            execution_mode=todo.execution_mode, conclusion=result.conclusion,
                            summary=result.summary, suspicious_count=len(result.matches),
                            excluded_count=result.excluded_count,
                            matches=[match.model_dump() for match in result.matches],
                            diagnostics=diagnostics, report_path=str(report_path),
                            rule_name=rule.name, rule_version=rule.config.version, rule_hash=rule.sha256,
                        ))
                        await db.commit()
                    _publish_event(task_id, "duplicate_pair_completed", {
                        "todo_id": todo.id, "display_name": todo.display_name,
                        "conclusion": result.conclusion, "suspicious_count": len(result.matches),
                    })
                    return True
                except Exception as exc:
                    logger.exception("duplicate pair failed: task=%s todo=%s", task_id, todo.id)
                    async with session_factory() as db:
                        current = (await db.execute(select(TodoItem).where(TodoItem.id == todo.id))).scalar_one()
                        current.status, current.completed_at = "failed", utc_now()
                        current.error_message = str(exc)[:2000]
                        await db.commit()
                    _publish_event(task_id, "duplicate_pair_failed", {
                        "todo_id": todo.id, "display_name": todo.display_name, "error": str(exc),
                    })
                    return False
                finally:
                    if usage_token is not None:
                        reset_usage_context(usage_token)

        results = await asyncio.wait_for(
            asyncio.gather(*(run_one(todo) for todo in todos)),
            timeout=settings.duplicate_check_total_timeout,
        )
        succeeded, failed = sum(bool(v) for v in results), sum(not v for v in results)
        async with session_factory() as db:
            task = (await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))).scalar_one()
            if is_task_cancelled(task_id):
                task.status = "cancelled"
            elif succeeded == 0:
                task.status, task.error_message = "failed", "所有文档对查重均失败"
            elif failed:
                task.status = "completed_with_warnings"
                task.error_message = f"{failed} 个文档对执行失败，可仅重试失败项"
            else:
                task.status = "completed"
            task.completed_at = utc_now()
            task.duration_seconds = utc_seconds_between(task.started_at, task.completed_at)
            final_status = task.status
            await db.commit()

        from backend.services.usage_summary import refresh_task_summary
        from backend.services.billing import settle_review_consumption
        await refresh_task_summary(task_id)
        await settle_review_consumption(task_id)
        _publish_event(task_id, "duplicate_task_completed", {
            "status": final_status, "completed_pairs": succeeded, "failed_pairs": failed,
        })
        return {"status": final_status, "completed_pairs": succeeded, "failed_pairs": failed}
    except Exception as exc:
        logger.exception("duplicate task failed: %s", task_id)
        final_status = "failed"
        completed_pairs = 0
        failed_pairs = 0
        try:
            async with session_factory() as db:
                task = (await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))).scalar_one_or_none()
                if task:
                    cancelled = is_task_cancelled(task_id)
                    completed_at = utc_now()
                    outstanding_status = "cancelled" if cancelled else "failed"
                    await db.execute(
                        update(TodoItem)
                        .where(
                            TodoItem.session_id == task_id,
                            TodoItem.todo_type == "duplicate_pair",
                            TodoItem.status.in_(["pending", "running"]),
                        )
                        .values(
                            status=outstanding_status,
                            completed_at=completed_at,
                            error_message=("任务已取消" if cancelled else str(exc)[:2000]),
                        )
                    )
                    completed_pairs = int(await db.scalar(select(func.count(TodoItem.id)).where(
                        TodoItem.session_id == task_id,
                        TodoItem.todo_type == "duplicate_pair",
                        TodoItem.status == "completed",
                    )) or 0)
                    failed_pairs = int(await db.scalar(select(func.count(TodoItem.id)).where(
                        TodoItem.session_id == task_id,
                        TodoItem.todo_type == "duplicate_pair",
                        TodoItem.status == "failed",
                    )) or 0)
                    if cancelled:
                        final_status = "cancelled"
                        task.error_message = "用户已取消查重任务"
                    elif completed_pairs:
                        final_status = "completed_with_warnings"
                        task.error_message = (
                            f"任务异常终止，已完成 {completed_pairs} 个文档对，"
                            f"{failed_pairs} 个文档对可重试：{str(exc)[:1200]}"
                        )
                    else:
                        final_status = "failed"
                        task.error_message = str(exc)[:2000]
                    task.status = final_status
                    task.completed_at = completed_at
                    if task.started_at:
                        task.duration_seconds = utc_seconds_between(task.started_at, task.completed_at)
                    await db.commit()
        finally:
            _publish_event(task_id, "error", {"message": str(exc)})
        try:
            from backend.services.usage_summary import refresh_task_summary
            from backend.services.billing import settle_review_consumption
            await refresh_task_summary(task_id)
            await settle_review_consumption(task_id)
        except Exception:
            logger.warning("failed to settle failed duplicate task %s", task_id, exc_info=True)
        _publish_event(task_id, "duplicate_task_completed", {
            "status": final_status,
            "completed_pairs": completed_pairs,
            "failed_pairs": failed_pairs,
        })
        return {
            "status": final_status,
            "message": str(exc),
            "completed_pairs": completed_pairs,
            "failed_pairs": failed_pairs,
        }
    finally:
        clear_task_cancelled(task_id)
        await engine.dispose()


@celery_app.task(bind=True, name="backend.tasks.duplicate_tasks.run_duplicate_check")
def run_duplicate_check(self, task_id: str) -> dict:
    return run_async(_run_duplicate(task_id))
