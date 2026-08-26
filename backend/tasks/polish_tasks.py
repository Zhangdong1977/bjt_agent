"""Celery task for VSTO-driven polish/expand/abbreviate requests."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from backend.celery_app import celery_app
from backend.models import PolishTask, User
from backend.services.llm_factory import create_llm_client
from backend.services.usage_recorder import instrument_llm_client
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

# Keep in sync with celery_app.task_annotations run_polish time_limit=300.
POLISH_MAX_RUNTIME_SECONDS = 4 * 60
POLISH_RESULT_MAX_CHARS = 60_000
_CANCEL_PREFIX = "polish:cancel:"
_cancel_local: set[str] = set()

POLISH_SYSTEM_PROMPT = (
    "你是一位精通招投标流程、商务写作和技术文档的资深专家，负责处理投标文件片段。"
    "只输出处理后的正文（纯 Markdown），不要输出解释、前言、结语或代码块围栏，不要重复原文标题。"
)

MODE_INSTRUCTIONS = {
    "expand": "请对片段进行专业扩写：保持原意与事实不变，补充展开论述、技术表述与逻辑衔接；不得虚构资质、业绩、数据。",
    "polish": "请对片段进行专业润色：优化语言表达、术语规范性与逻辑衔接；不改变任何事实与数据；篇幅与原文相当。",
    "abbreviate": "请对片段进行专业缩写：保留关键信息与结论，压缩冗余表述；不得丢失重要承诺或数据。",
}


def _redis():
    from backend.config import get_settings

    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        import redis

        return redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5.0)
    except Exception:
        return None


def set_polish_cancelled(task_id: str) -> None:
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


def is_polish_cancelled(task_id: str) -> bool:
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


def clear_polish_cancelled(task_id: str) -> None:
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


def _strip_code_fence(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            value = "\n".join(lines[1:-1]).strip()
    return value


def build_messages(snapshot: dict[str, Any]):
    from mini_agent.schema import Message

    instruction = MODE_INSTRUCTIONS.get(snapshot.get("mode")) or MODE_INSTRUCTIONS["polish"]
    lines = [f"处理方式：{instruction}"]
    requirements = str(snapshot.get("requirements") or "").strip()
    lines.append(f"补充要求：{requirements or '无'}")
    target_length = snapshot.get("target_length")
    if target_length:
        lines.append(f"目标篇幅：约 {int(target_length)} 字")
    lines.append("")
    lines.append("片段：")
    lines.append(str(snapshot.get("input_text") or ""))
    return [
        Message(role="system", content=POLISH_SYSTEM_PROMPT),
        Message(role="user", content="\n".join(lines)),
    ]


async def _fail_polish(session_factory, task_id: str, message: str) -> dict[str, Any]:
    async with session_factory() as db:
        task = (
            await db.execute(select(PolishTask).where(PolishTask.id == task_id))
        ).scalar_one_or_none()
        if task and task.status != "cancelled":
            task.status = "failed"
            task.error_message = message[:2_000]
            task.completed_at = utc_now()
            await db.commit()
    return {"status": "error", "message": message}


async def _run_polish(task_id: str) -> dict[str, Any]:
    from backend.tasks.review_tasks import create_session_factory

    session_factory, engine = create_session_factory()
    try:
        async with session_factory() as db:
            from backend.services.task_lifecycle import claim_task_for_execution

            task = await claim_task_for_execution(db, task_kind="polish", task_id=task_id)
            if task is None:
                return {"status": "ignored", "message": "任务不存在、已结束或已由其他 worker 认领"}
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
            snapshot = {
                "mode": task.mode,
                "input_text": task.input_text,
                "requirements": task.requirements,
                "target_length": task.target_length,
            }

        from backend.services.usage_context import (
            UsageContext,
            reset_usage_context,
            set_usage_context,
        )

        usage_token = set_usage_context(
            UsageContext(**usage_identity, project_id=None, task_id=task_id, todo_id=None)
        )
        try:
            client = instrument_llm_client(create_llm_client(timeout=120))
            response = await asyncio.wait_for(
                client.generate(messages=build_messages(snapshot)),
                timeout=POLISH_MAX_RUNTIME_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("润色超过系统允许的最长执行时间") from exc
        finally:
            reset_usage_context(usage_token)

        content = _strip_code_fence(str(getattr(response, "content", "") or ""))
        if not content:
            raise RuntimeError("模型未返回有效内容")

        if is_polish_cancelled(task_id):
            async with session_factory() as db:
                task = (
                    await db.execute(select(PolishTask).where(PolishTask.id == task_id))
                ).scalar_one_or_none()
                if task and task.status != "completed":
                    task.status = "cancelled"
                    task.error_message = "用户取消了润色任务"
                    task.completed_at = utc_now()
                    await db.commit()
            return {"status": "cancelled"}

        async with session_factory() as db:
            task = (
                await db.execute(select(PolishTask).where(PolishTask.id == task_id))
            ).scalar_one_or_none()
            if task is None:
                return {"status": "error", "message": "润色任务不存在"}
            if task.status == "cancelled":
                return {"status": "cancelled"}
            task.result_text = content[:POLISH_RESULT_MAX_CHARS]
            task.status = "completed"
            task.completed_at = utc_now()
            await db.commit()
        return {"status": "completed", "result_chars": len(content)}
    except Exception as exc:
        logger.exception("Polish task %s failed", task_id)
        return await _fail_polish(session_factory, task_id, str(exc))
    finally:
        from backend.services.task_lifecycle import finalize_task_usage

        try:
            await finalize_task_usage("polish", task_id)
        except Exception:
            logger.exception("Could not finalize polish usage: task=%s", task_id)
        try:
            clear_polish_cancelled(task_id)
        except Exception:
            logger.exception("Could not clear polish cancellation flag: task=%s", task_id)
        await engine.dispose()


@celery_app.task(bind=True, name="backend.tasks.polish_tasks.run_polish")
def run_polish(self, task_id: str) -> dict[str, Any]:
    """Celery entry point."""
    return asyncio.run(_run_polish(task_id))
