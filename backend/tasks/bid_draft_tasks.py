"""Celery task for bid draft generation (招标解析 → 标书生成)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.agent.bid_draft_agent import BidDraftAgent, BidDraftCancelled
from backend.celery_app import celery_app
from backend.config import get_settings
from backend.models import BidDraftTask, Document, User
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

# Keep below celery_app.task_annotations run_bid_draft soft_time_limit=6900.
BID_DRAFT_MAX_RUNTIME_SECONDS = 110 * 60
_CANCEL_PREFIX = "bid-draft:cancel:"
_cancel_local: set[str] = set()


def _redis():
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        import redis

        return redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5.0)
    except Exception:
        return None


def set_bid_draft_cancelled(task_id: str) -> None:
    _cancel_local.add(task_id)
    client = _redis()
    if client is not None:
        try:
            client.set(f"{_CANCEL_PREFIX}{task_id}", "1", ex=7200)
        finally:
            try:
                client.close()
            except Exception:
                pass


def is_bid_draft_cancelled(task_id: str) -> bool:
    if task_id in _cancel_local:
        return True
    client = _redis()
    if client is not None:
        try:
            return client.exists(f"{_CANCEL_PREFIX}{task_id}") == 1
        finally:
            try:
                client.close()
            except Exception:
                pass
    return False


def clear_bid_draft_cancelled(task_id: str) -> None:
    _cancel_local.discard(task_id)
    client = _redis()
    if client is not None:
        try:
            client.delete(f"{_CANCEL_PREFIX}{task_id}")
        finally:
            try:
                client.close()
            except Exception:
                pass


def _publish(task_id: str, event_type: str, data: dict[str, Any]) -> None:
    # Reuse the production Redis Streams/SSE implementation.
    from backend.tasks.review_tasks import _publish_event

    _publish_event(task_id, event_type, data)


def _bound_event(data: dict[str, Any]) -> dict[str, Any]:
    """Keep progress events metadata-sized; section bodies stay in the DB."""
    bounded: dict[str, Any] = {}
    for key, value in (data or {}).items():
        if isinstance(value, str):
            bounded[str(key)[:100]] = value[:2_000]
        elif value is None or isinstance(value, (bool, int, float)):
            bounded[str(key)[:100]] = value
        else:
            bounded[str(key)[:100]] = str(value)[:2_000]
    return bounded


def _new_session_factory():
    from backend.tasks.review_tasks import create_session_factory

    return create_session_factory()


async def _cancel_watcher(task_id: str, event: asyncio.Event) -> None:
    while not event.is_set():
        if is_bid_draft_cancelled(task_id):
            event.set()
            return
        await asyncio.sleep(1.0)


def _read_tender_markdown(document: Document) -> str:
    path = Path(document.parsed_markdown_path)
    if not path.is_absolute():
        path = Path(get_settings().workspace_path) / path
    return path.read_text(encoding="utf-8", errors="replace")


async def _fail_bid_draft(session_factory, task_id: str, message: str, publish: bool = False) -> dict[str, Any]:
    async with session_factory() as db:
        task = (
            await db.execute(select(BidDraftTask).where(BidDraftTask.id == task_id))
        ).scalar_one_or_none()
        if task and task.status != "cancelled":
            task.status = "failed"
            task.error_message = message[:2_000]
            task.completed_at = utc_now()
            await db.commit()
    if publish:
        _publish(task_id, "error", {"message": message[:2_000]})
        _publish(task_id, "status", {"status": "failed"})
    return {"status": "error", "message": message}


async def _mark_cancelled(session_factory, task_id: str) -> dict[str, Any]:
    async with session_factory() as db:
        task = (
            await db.execute(select(BidDraftTask).where(BidDraftTask.id == task_id))
        ).scalar_one_or_none()
        if task and task.status not in {"completed"}:
            task.status = "cancelled"
            task.error_message = "用户取消了标书生成任务"
            task.completed_at = utc_now()
            await db.commit()
    _publish(task_id, "status", {"status": "cancelled"})
    return {"status": "cancelled"}


async def _run_bid_draft(task_id: str) -> dict[str, Any]:
    session_factory, engine = _new_session_factory()
    cancel_event = asyncio.Event()
    watcher = asyncio.create_task(_cancel_watcher(task_id, cancel_event))
    try:
        async with session_factory() as db:
            from backend.services.task_lifecycle import claim_task_for_execution

            task = await claim_task_for_execution(db, task_kind="bid_draft", task_id=task_id)
            if task is None:
                return {"status": "ignored", "message": "任务不存在、已结束或已由其他 worker 认领"}
            if not task.tender_document_id:
                return await _fail_bid_draft(session_factory, task_id, "任务缺少招标文件")
            document = (
                await db.execute(select(Document).where(Document.id == task.tender_document_id))
            ).scalar_one_or_none()
            if (
                document is None
                or document.status != "parsed"
                or not document.parsed_markdown_path
            ):
                return await _fail_bid_draft(
                    session_factory, task_id, "招标文件尚未解析完成，请稍后重试或重新上传"
                )
            try:
                markdown = _read_tender_markdown(document)
            except Exception as exc:
                return await _fail_bid_draft(
                    session_factory, task_id, f"招标文件解析产物读取失败: {exc}"
                )
            if not markdown.strip():
                return await _fail_bid_draft(session_factory, task_id, "招标文件解析结果为空")
            user = (
                await db.execute(select(User).where(User.id == task.user_id))
            ).scalar_one_or_none()
            usage_identity = {
                "external_user_id": user.external_user_id if user else None,
                "local_user_id": task.user_id,
                "user_name": (user.username if user else task.user_id) or task.user_id,
                "enterprise_name": user.enterprise_name if user else None,
                "interior_user": bool(user.interior_user) if user else False,
            }
            workspace_dir = Path(get_settings().workspace_path) / "bid-draft" / task_id

        _publish(task_id, "status", {"status": "running"})

        def event_callback(event_type: str, data: dict[str, Any]) -> None:
            _publish(task_id, event_type, _bound_event(data))

        agent = BidDraftAgent(
            task_id=task_id,
            tender_markdown=markdown,
            workspace_dir=workspace_dir,
            session_factory=session_factory,
            event_callback=event_callback,
            cancel_event=cancel_event,
        )

        from backend.services.usage_context import (
            UsageContext,
            reset_usage_context,
            set_usage_context,
        )

        usage_token = set_usage_context(
            UsageContext(**usage_identity, project_id=None, task_id=task_id, todo_id=None)
        )
        try:
            try:
                summary = await asyncio.wait_for(agent.run(), timeout=BID_DRAFT_MAX_RUNTIME_SECONDS)
            except asyncio.TimeoutError as exc:
                raise RuntimeError("标书生成超过系统允许的最长执行时间") from exc
        finally:
            reset_usage_context(usage_token)

        if cancel_event.is_set() or is_bid_draft_cancelled(task_id):
            return await _mark_cancelled(session_factory, task_id)

        async with session_factory() as db:
            task = (
                await db.execute(select(BidDraftTask).where(BidDraftTask.id == task_id))
            ).scalar_one_or_none()
            if task is None:
                return {"status": "error", "message": "标书生成任务不存在"}
            if task.status == "cancelled":
                return {"status": "cancelled"}
            task.status = "completed"
            task.completed_at = utc_now()
            await db.commit()

        _publish(task_id, "result", {"status": "completed", "summary": _bound_event(summary or {})})
        _publish(task_id, "status", {"status": "completed"})
        return {"status": "completed", "summary": summary}
    except BidDraftCancelled:
        return await _mark_cancelled(session_factory, task_id)
    except Exception as exc:
        logger.exception("Bid draft task %s failed", task_id)
        return await _fail_bid_draft(session_factory, task_id, str(exc), publish=True)
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        from backend.services.task_lifecycle import finalize_task_usage

        try:
            await finalize_task_usage("bid_draft", task_id)
        except Exception:
            logger.exception("Could not finalize bid-draft usage: task=%s", task_id)
        try:
            clear_bid_draft_cancelled(task_id)
        except Exception:
            logger.exception("Could not clear bid-draft cancellation flag: task=%s", task_id)
        await engine.dispose()


@celery_app.task(bind=True, name="backend.tasks.bid_draft_tasks.run_bid_draft")
def run_bid_draft(self, task_id: str) -> dict[str, Any]:
    """Celery entry point."""
    return asyncio.run(_run_bid_draft(task_id))
