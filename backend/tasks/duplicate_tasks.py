"""Celery task for rule-driven technical bid duplicate checking."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from backend.agent.duplicate_master_agent import DuplicateMasterAgent
from backend.celery_app import celery_app
from backend.config import get_settings
from backend.models import Document, Project, ReviewTask
from backend.services.duplicate_candidates import (
    DocumentDescriptor,
    DuplicateCandidateService,
)
from backend.tasks.review_tasks import (
    _progress_watchdog,
    _publish_event,
    clear_task_cancelled,
    create_session_factory,
    is_task_cancelled,
    run_async,
)
from backend.utils.time_utils import ensure_utc_aware, utc_now, utc_seconds_between

logger = logging.getLogger(__name__)


async def _cancellation_monitor(session_factory, task_id: str, cancel_event: asyncio.Event):
    """Mirror review-agent cancellation and frontend heartbeat semantics."""
    while not cancel_event.is_set():
        await asyncio.sleep(5)
        if is_task_cancelled(task_id):
            cancel_event.set()
            return
        async with session_factory() as db:
            task = (
                await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
            ).scalar_one_or_none()
            if task is None or task.status not in {"pending", "running"}:
                cancel_event.set()
                return
            if task.last_heartbeat:
                elapsed = (utc_now() - ensure_utc_aware(task.last_heartbeat)).total_seconds()
                if elapsed > 60:
                    _publish_event(
                        task_id,
                        "error",
                        {"message": "超过 60 秒未收到页面心跳，查重任务已自动停止"},
                        session_factory=session_factory,
                    )
                    cancel_event.set()
                    return


@celery_app.task(bind=True, name="backend.tasks.duplicate_tasks.run_duplicate_check")
def run_duplicate_check(self, task_id: str) -> dict:
    """Execute one duplicate task on the existing review worker queue."""

    async def _run() -> dict:
        settings = get_settings()
        session_factory, engine = create_session_factory()
        cancel_event = asyncio.Event()
        try:
            async with session_factory() as db:
                task = (
                    await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
                ).scalar_one_or_none()
                if task is None or task.task_type != "duplicate":
                    return {"status": "error", "message": "查重任务不存在"}
                project = (
                    await db.execute(select(Project).where(Project.id == task.project_id))
                ).scalar_one_or_none()
                if project is None or project.project_type != "duplicate":
                    raise ValueError("查重项目不存在或项目类型错误")
                documents = list(
                    (
                        await db.execute(
                            select(Document).where(Document.project_id == project.id)
                        )
                    ).scalars().all()
                )
                left_docs = [d for d in documents if d.doc_type == "duplicate_left"]
                right_docs = [d for d in documents if d.doc_type == "duplicate_right"]
                if len(left_docs) != 1 or len(right_docs) != 1:
                    raise ValueError("查重项目必须且只能包含一份 A 方和一份 B 方技术应标书")
                left, right = left_docs[0], right_docs[0]
                for side_name, document in (("A方", left), ("B方", right)):
                    parsed_path = document.parsed_markdown_path or document.parsed_html_path
                    if document.status != "parsed" or not parsed_path or not Path(parsed_path).exists():
                        raise ValueError(f"{side_name}技术应标书尚未解析完成或解析结果不存在")

                task.status = "running"
                task.started_at = utc_now()
                task.last_heartbeat = utc_now()
                await db.commit()
                project_id = str(project.id)
                user_id = str(project.user_id)
                max_concurrency = task.max_concurrency
                left_descriptor = DocumentDescriptor(
                    id=left.id,
                    filename=left.original_filename,
                    path=left.parsed_markdown_path or left.parsed_html_path,
                )
                right_descriptor = DocumentDescriptor(
                    id=right.id,
                    filename=right.original_filename,
                    path=right.parsed_markdown_path or right.parsed_html_path,
                )

            _publish_event(task_id, "status", {"status": "running"})
            _publish_event(task_id, "progress", {"message": "正在构建 A/B 文档查重候选索引"})

            candidate_service = DuplicateCandidateService(left_descriptor, right_descriptor)
            # Similarity is calculated locally so it is deterministic and does
            # not introduce unrecorded embedding-provider cost.
            await candidate_service.build()
            cache_path = (
                settings.workspace_path
                / user_id
                / project_id
                / task_id
                / "duplicate_candidates.json"
            )
            candidate_service.save_cache(cache_path)
            _publish_event(
                task_id,
                "progress",
                {"message": f"候选索引构建完成，共 {len(candidate_service.candidates)} 对"},
            )

            def event_cb(event_type: str, data: dict):
                _publish_event(
                    task_id,
                    event_type,
                    data,
                    session_factory=session_factory,
                )

            master = DuplicateMasterAgent(
                project_id=project_id,
                task_id=task_id,
                user_id=user_id,
                rule_library_path=str(settings.duplicate_rule_library_path),
                left_document_id=left_descriptor.id,
                right_document_id=right_descriptor.id,
                candidate_service=candidate_service,
                session_factory=session_factory,
                max_concurrency=max_concurrency,
                event_callback=event_cb,
                cancel_event=cancel_event,
            )

            watchdog = asyncio.create_task(
                _progress_watchdog(task_id, cancel_event, operation_name="查重")
            )
            cancel_monitor = asyncio.create_task(
                _cancellation_monitor(session_factory, task_id, cancel_event)
            )
            try:
                try:
                    result = await asyncio.wait_for(
                        master.run(), timeout=settings.agent_total_timeout
                    )
                except asyncio.TimeoutError as exc:
                    cancel_event.set()
                    raise TimeoutError(
                        f"查重执行超时（超过 {settings.agent_total_timeout // 60} 分钟）"
                    ) from exc
            finally:
                watchdog.cancel()
                cancel_monitor.cancel()
                await asyncio.gather(watchdog, cancel_monitor, return_exceptions=True)

            if cancel_event.is_set() or is_task_cancelled(task_id):
                raise asyncio.CancelledError()
            if not result.get("success"):
                raise RuntimeError(result.get("error") or "查重主代理执行失败")

            async with session_factory() as db:
                task = (
                    await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
                ).scalar_one()
                task.status = "completed"
                task.completed_at = utc_now()
                task.duration_seconds = utc_seconds_between(task.started_at, task.completed_at)
                await db.commit()

            from backend.services.usage_summary import refresh_task_summary
            from backend.services.billing import settle_review_consumption

            await refresh_task_summary(task_id)
            await settle_review_consumption(task_id)
            finding_count = int(result.get("stats", {}).get("finding_count", 0))
            _publish_event(
                task_id,
                "complete",
                {"status": "completed", "findings_count": finding_count},
            )
            return {"status": "success", "finding_count": finding_count}

        except asyncio.CancelledError:
            async with session_factory() as db:
                task = (
                    await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
                ).scalar_one_or_none()
                if task:
                    task.status = "cancelled"
                    task.error_message = "查重任务已取消"
                    task.completed_at = utc_now()
                    if task.started_at:
                        task.duration_seconds = utc_seconds_between(
                            task.started_at, task.completed_at
                        )
                    await db.commit()
            _publish_event(task_id, "error", {"message": "查重任务已取消"})
            return {"status": "cancelled"}
        except Exception as exc:
            logger.exception("Duplicate task failed: task_id=%s", task_id)
            async with session_factory() as db:
                task = (
                    await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
                ).scalar_one_or_none()
                if task:
                    task.status = "failed"
                    task.error_message = str(exc)
                    task.completed_at = utc_now()
                    if task.started_at:
                        task.duration_seconds = utc_seconds_between(
                            task.started_at, task.completed_at
                        )
                    await db.commit()
            from backend.services.usage_summary import refresh_task_summary

            await refresh_task_summary(task_id)
            _publish_event(task_id, "error", {"message": str(exc)})
            return {"status": "error", "message": str(exc)}
        finally:
            clear_task_cancelled(task_id)
            await engine.dispose()

    return run_async(_run())
