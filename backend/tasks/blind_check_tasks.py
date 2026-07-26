"""Celery task for live-document blind-mark compliance checks."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import select

from backend.agent.blind_check_agent import BlindCheckAgent
from backend.celery_app import celery_app
from backend.config import get_settings
from backend.models import BlindCheckFinding, BlindCheckTask, VstoToolSession
from backend.utils.time_utils import utc_now, utc_seconds_between

logger = logging.getLogger(__name__)

_CANCEL_PREFIX = "blind-check:cancel:"
_cancel_local: set[str] = set()
BLIND_CHECK_MAX_RUNTIME_SECONDS = 25 * 60


def _redis():
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        import redis

        return redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5.0)
    except Exception:
        return None


def set_blind_check_cancelled(task_id: str) -> None:
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


def is_blind_check_cancelled(task_id: str) -> bool:
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


def clear_blind_check_cancelled(task_id: str) -> None:
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
    # Reuse the production Redis Streams/SSE implementation so the new page
    # behaves exactly like existing review pages.
    from backend.tasks.review_tasks import _publish_event

    _publish_event(task_id, event_type, data)


def _safe_progress_value(value: Any, depth: int = 0) -> Any:
    """Bound nested agent/tool event data before it enters Redis/SSE."""
    if depth >= 4:
        return str(value)[:2_000]
    if isinstance(value, str):
        return value[:5_000]
    if isinstance(value, dict):
        return {
            str(key)[:100]: _safe_progress_value(item, depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_progress_value(item, depth + 1) for item in list(value)[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:2_000]


def _safe_progress_event(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Remove document/model contents that the progress UI does not need.

    ``vsto_tool_request`` is the one exception: its validated arguments are the
    actual function-call envelope that the page must relay to Word.  Generic
    Mini-Agent events only expose status metadata, never prompt text, thinking,
    tool arguments, or document evidence.
    """
    data = data or {}
    if event_type == "vsto_tool_request":
        # This is the executable, already allow-listed/size-validated envelope.
        # Preserve its arguments exactly; truncating a long requirement here
        # would make Word inspect a different rule set from the one the agent
        # is evaluating.
        return {
            "call_id": str(data.get("call_id") or "")[:36],
            "tool_session_id": str(data.get("tool_session_id") or "")[:36],
            "tool": str(data.get("tool") or "")[:100],
            "arguments": data.get("arguments") if isinstance(data.get("arguments"), dict) else {},
            "expires_at": str(data.get("expires_at") or "")[:100],
        }
    if event_type == "vsto_tool_result":
        return {
            "call_id": str(data.get("call_id") or "")[:36],
            "success": bool(data.get("success")),
        }
    if event_type == "llm_output":
        tool_calls = data.get("tool_calls") if isinstance(data.get("tool_calls"), list) else []
        return {
            "step": data.get("step"),
            "has_content": bool(data.get("content")),
            "has_thinking": bool(data.get("thinking")),
            "tools": [
                str(item.get("name") or "")[:100]
                for item in tool_calls[:20]
                if isinstance(item, dict)
            ],
        }
    if event_type in {"tool_call_start", "tool_call_end"}:
        safe = {
            "step": data.get("step"),
            "tool": str(data.get("tool") or "")[:100],
        }
        if event_type == "tool_call_end":
            safe["success"] = bool(data.get("success"))
            if not safe["success"] and data.get("error"):
                safe["error"] = str(data.get("error"))[:500]
        return safe
    return _safe_progress_value(data)


def _new_session_factory():
    from backend.tasks.review_tasks import create_session_factory

    return create_session_factory()


async def _cancel_watcher(task_id: str, event: asyncio.Event) -> None:
    while not event.is_set():
        if is_blind_check_cancelled(task_id):
            event.set()
            return
        await asyncio.sleep(1.0)


def _finding_kwargs(task_id: str, value: dict[str, Any]) -> dict[str, Any]:
    """Clamp agent output before writing it to the database."""
    return {
        "task_id": task_id,
        "category": str(value.get("category") or "other")[:40],
        "severity": str(value.get("severity") or "info")[:20],
        "verdict": str(value.get("verdict") or "unknown")[:20],
        "title": str(value.get("title") or "未命名检查项")[:255],
        "description": str(value.get("description") or "未提供判断说明")[:10_000],
        "evidence_text": str(value.get("evidence_text") or "")[:5_000] or None,
        "page_number": value.get("page_number"),
        "paragraph_index": value.get("paragraph_index"),
        "location": value.get("location") if isinstance(value.get("location"), dict) else {},
        "rule_reference": str(value.get("rule_reference") or "")[:5_000] or None,
        "confidence": value.get("confidence"),
    }


async def _run_blind_check(task_id: str) -> dict[str, Any]:
    session_factory, engine = _new_session_factory()
    cancel_event = asyncio.Event()
    watcher = asyncio.create_task(_cancel_watcher(task_id, cancel_event))
    try:
        async with session_factory() as db:
            task = (
                await db.execute(select(BlindCheckTask).where(BlindCheckTask.id == task_id))
            ).scalar_one_or_none()
            if task is None:
                return {"status": "error", "message": "暗标检查任务不存在"}
            if task.status == "cancelled":
                return {"status": "cancelled", "message": "任务已取消"}
            if task.status not in {"created", "waiting_for_document"}:
                return {"status": task.status, "message": "任务已由其他执行流程结束"}
            session = (
                await db.execute(
                    select(VstoToolSession).where(VstoToolSession.id == task.tool_session_id)
                )
            ).scalar_one_or_none()
            if session is None:
                task.status = "failed"
                task.error_message = "VSTO 工具会话不存在"
                task.completed_at = utc_now()
                await db.commit()
                return {"status": "error", "message": task.error_message}
            if session.status != "active" or session.expires_at <= utc_now():
                task.status = "failed"
                task.error_message = "VSTO 工具会话已失效，请重新打开 Word 页面"
                task.completed_at = utc_now()
                await db.commit()
                _publish(task_id, "status", {"status": "failed", "message": task.error_message})
                return {"status": "error", "message": task.error_message}
            if task.snapshot_id and session.snapshot_id and task.snapshot_id != session.snapshot_id:
                task.status = "failed"
                task.error_message = "Word 文档快照与工具会话不一致，请重新检查"
                task.completed_at = utc_now()
                await db.commit()
                _publish(task_id, "status", {"status": "failed", "message": task.error_message})
                return {"status": "error", "message": task.error_message}
            now = utc_now()
            task.status = "running"
            task.started_at = task.started_at or now
            await db.commit()

        _publish(task_id, "status", {"status": "running", "phase": "agent_started"})

        def event_callback(event_type: str, data: dict[str, Any]) -> None:
            # Do not put full document contents into the event stream.  The
            # worker receives the complete result through the broker/DB; SSE is
            # only a progress channel and keeps short evidence previews.
            safe = _safe_progress_event(event_type, data or {})
            _publish(task_id, event_type, safe)

        async with session_factory() as db:
            task = (
                await db.execute(select(BlindCheckTask).where(BlindCheckTask.id == task_id))
            ).scalar_one()
            agent = BlindCheckAgent(
                task_id=task.id,
                tool_session_id=task.tool_session_id,
                requirement_text=task.requirement_text,
                session_factory=session_factory,
                event_callback=event_callback,
                cancel_event=cancel_event,
                snapshot_id=task.snapshot_id,
                scope=task.scope,
            )

        try:
            result = await asyncio.wait_for(
                agent.run_blind_check(),
                timeout=min(
                    get_settings().agent_total_timeout,
                    BLIND_CHECK_MAX_RUNTIME_SECONDS,
                ),
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("暗标检查超过系统允许的最长执行时间") from exc

        if cancel_event.is_set() or is_blind_check_cancelled(task_id):
            async with session_factory() as db:
                task = (await db.execute(select(BlindCheckTask).where(BlindCheckTask.id == task_id))).scalar_one_or_none()
                if task:
                    task.status = "cancelled"
                    task.error_message = "用户取消了暗标检查"
                    task.completed_at = utc_now()
                    await db.commit()
            _publish(task_id, "status", {"status": "cancelled"})
            return {"status": "cancelled"}

        findings = result.get("findings") if isinstance(result, dict) else []
        findings = findings if isinstance(findings, list) else []
        summary = result.get("summary") if isinstance(result, dict) else None
        if not isinstance(summary, dict):
            summary = {"overall": "unknown", "critical": 0, "major": 0, "minor": 0, "unknown": 1}

        async with session_factory() as db:
            task = (await db.execute(select(BlindCheckTask).where(BlindCheckTask.id == task_id))).scalar_one_or_none()
            if task is None:
                return {"status": "error", "message": "暗标检查任务不存在"}
            if task.status == "cancelled":
                return {"status": "cancelled"}
            task.summary = summary
            task.status = "completed"
            task.completed_at = utc_now()
            if task.started_at and task.completed_at:
                # Duration is not a model field; keep it in summary for the
                # first phase without changing existing task tables.
                summary["duration_seconds"] = utc_seconds_between(task.started_at, task.completed_at)
            for item in findings[:200]:
                if isinstance(item, dict):
                    db.add(BlindCheckFinding(**_finding_kwargs(task_id, item)))
            await db.commit()

        _publish(task_id, "result", {"status": "completed", "summary": summary, "finding_count": len(findings)})
        _publish(task_id, "status", {"status": "completed"})
        return {"status": "completed", "summary": summary, "finding_count": len(findings)}
    except Exception as exc:
        logger.exception("Blind check task %s failed", task_id)
        message = str(exc)[:2_000]
        async with session_factory() as db:
            task = (await db.execute(select(BlindCheckTask).where(BlindCheckTask.id == task_id))).scalar_one_or_none()
            if task and task.status != "cancelled":
                task.status = "failed"
                task.error_message = message
                task.completed_at = utc_now()
                await db.commit()
        _publish(task_id, "error", {"message": message})
        _publish(task_id, "status", {"status": "failed"})
        return {"status": "error", "message": message}
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        clear_blind_check_cancelled(task_id)
        await engine.dispose()


@celery_app.task(bind=True, name="backend.tasks.blind_check_tasks.run_blind_check")
def run_blind_check(self, task_id: str) -> dict[str, Any]:
    """Celery entry point."""
    return asyncio.run(_run_blind_check(task_id))
