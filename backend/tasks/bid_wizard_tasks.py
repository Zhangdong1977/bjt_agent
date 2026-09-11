"""Celery tasks for AI编标（bid-wizard）：素材索引 + 逐章撰写.

骨架照 bid_draft_tasks.py：claim → run → finally finalize_task_usage；
取消经 Redis 标记；SSE 复用 review_tasks._publish_event。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.agent.bid_wizard_agent import (
    BidWizardCancelled,
    BidWizardWriteAgent,
    WizardLLM,
    analyze_tender,
    split_material_chunks,
    write_material_index,
)
from backend.celery_app import celery_app
from backend.config import get_settings
from backend.models import (
    BidWizardIndexTask,
    BidWizardMaterial,
    BidWritingTask,
    Document,
    User,
)
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

# Keep below celery_app.task_annotations soft_time_limits.
BID_WIZARD_WRITE_MAX_RUNTIME_SECONDS = 110 * 60
BID_WIZARD_INDEX_MAX_RUNTIME_SECONDS = 20 * 60
_INDEX_RETRY_COUNTDOWN = 30
_INDEX_RETRY_MAX = 20  # 30s × 20 ≈ 10 分钟等待解析完成

_CANCEL_PREFIX = "bid-wizard:cancel:"
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


def set_wizard_write_cancelled(task_id: str) -> None:
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


def is_wizard_write_cancelled(task_id: str) -> bool:
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


def clear_wizard_write_cancelled(task_id: str) -> None:
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
    from backend.tasks.review_tasks import _publish_event

    _publish_event(task_id, event_type, data)


def _new_session_factory():
    from backend.tasks.review_tasks import create_session_factory

    return create_session_factory()


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        path = Path(get_settings().workspace_path) / path
    return path


# ------------------------------------------------------------------ index


async def _run_index(index_task_id: str) -> dict[str, Any]:
    session_factory, engine = _new_session_factory()
    try:
        from backend.services.task_lifecycle import claim_task_for_execution

        # 上传后立即建索引任务，但 parse_document 还在 parser 队列跑——
        # 未解析完先自重试等待（决策 17a：上传即自动索引）。
        # 等待检查必须放在认领**之前**：若先认领（pending→running）再抛 _AwaitParse，
        # autoretry 重入时 claim 只接受 pending，会把重试拒成 ignored、任务行永久卡住。
        async with session_factory() as db:
            task_row = (
                await db.execute(
                    select(BidWizardIndexTask).where(BidWizardIndexTask.id == index_task_id)
                )
            ).scalar_one_or_none()
            if task_row is None:
                return {"status": "ignored", "message": "任务不存在或已删除"}
            material = (
                await db.execute(
                    select(BidWizardMaterial).where(BidWizardMaterial.id == task_row.material_id)
                )
            ).scalar_one_or_none()
            if material is None:
                raise RuntimeError("素材已删除")
            document = (
                await db.execute(select(Document).where(Document.id == material.document_id))
            ).scalar_one_or_none()
            user = (
                await db.execute(select(User).where(User.id == task_row.user_id))
            ).scalar_one_or_none()
        if document is None:
            raise RuntimeError("素材文档不存在")
        if document.status in ("pending", "parsing"):
            raise _AwaitParse()
        if document.status != "parsed" or not document.parsed_markdown_path:
            raise RuntimeError(f"素材解析未成功（状态 {document.status}），请删除后重新上传")

        async with session_factory() as db:
            task = await claim_task_for_execution(
                db, task_kind="bid_wizard_index", task_id=index_task_id
            )
            if task is None:
                return {"status": "ignored", "message": "任务不存在、已结束或已由其他 worker 认领"}
        markdown = _resolve(document.parsed_markdown_path).read_text(
            encoding="utf-8", errors="replace"
        )
        if not markdown.strip():
            raise RuntimeError("素材解析结果为空")

        from backend.services.usage_context import (
            UsageContext,
            reset_usage_context,
            set_usage_context,
        )

        usage_identity = {
            "external_user_id": user.external_user_id if user else None,
            "local_user_id": task.user_id,
            "user_name": (user.username if user else task.user_id) or task.user_id,
            "enterprise_name": user.enterprise_name if user else None,
            "interior_user": bool(user.interior_user) if user else False,
        }
        usage_token = set_usage_context(
            UsageContext(**usage_identity, project_id=None, task_id=index_task_id, todo_id=None)
        )

        async def _mark_material(**fields: Any) -> None:
            async with session_factory() as db:
                row = (
                    await db.execute(
                        select(BidWizardMaterial).where(BidWizardMaterial.id == material.id)
                    )
                ).scalar_one()
                for key, value in fields.items():
                    setattr(row, key, value)
                await db.commit()

        try:
            await _mark_material(index_status="indexing")
            llm = WizardLLM()
            chunks = split_material_chunks(markdown)
            if not chunks:
                raise RuntimeError("素材分段结果为空")
            chunk_list = "\n\n".join(
                f"#<no> {chunk['no']} {chunk.get('heading') or ''}\n{chunk['text'][:600]}"
                for chunk in chunks
            )
            from backend.agent.bid_wizard_agent import (
                INDEX_META_SYSTEM_PROMPT,
                INDEX_META_USER_TEMPLATE,
            )

            metas_raw = await asyncio.wait_for(
                llm.generate_json(
                    INDEX_META_SYSTEM_PROMPT,
                    INDEX_META_USER_TEMPLATE.replace("__CHUNK_LIST__", chunk_list),
                ),
                timeout=240,
            )
            metas = {
                int(item.get("no") or 0): item
                for item in (metas_raw if isinstance(metas_raw, list) else [])
                if isinstance(item, dict)
            }
            write_material_index(
                get_settings().workspace_path, task.wizard_id, material.document_id, chunks, metas
            )
            await _mark_material(
                index_status="indexed",
                indexed_at=utc_now(),
                index_error=None,
                chunk_count=len(chunks),
            )
            async with session_factory() as db:
                task_row = (
                    await db.execute(
                        select(BidWizardIndexTask).where(BidWizardIndexTask.id == index_task_id)
                    )
                ).scalar_one()
                task_row.status = "completed"
                task_row.completed_at = utc_now()
                await db.commit()
            return {"status": "completed", "chunk_count": len(chunks)}
        finally:
            reset_usage_context(usage_token)
    except _AwaitParse:
        # 交给 celery 自重试（见 run_bid_wizard_index 的 except 分支）
        raise
    except Exception as exc:
        logger.exception("bid-wizard index task %s failed", index_task_id)
        try:
            async with session_factory() as db:
                task_row = (
                    await db.execute(
                        select(BidWizardIndexTask).where(BidWizardIndexTask.id == index_task_id)
                    )
                ).scalar_one_or_none()
                if task_row and task_row.status != "cancelled":
                    task_row.status = "failed"
                    task_row.error_message = str(exc)[:2_000]
                    task_row.completed_at = utc_now()
                    await db.commit()
                material_row = (
                    await db.execute(
                        select(BidWizardMaterial).where(
                            BidWizardMaterial.id == task_row.material_id
                        )
                    )
                ).scalar_one_or_none()
                if material_row is not None:
                    material_row.index_status = "failed"
                    material_row.index_error = str(exc)[:2_000]
                    await db.commit()
        except Exception:
            logger.exception("bid-wizard index failure mark failed: %s", index_task_id)
        return {"status": "error", "message": str(exc)}
    finally:
        from backend.services.task_lifecycle import finalize_task_usage

        try:
            await finalize_task_usage("bid_wizard_index", index_task_id)
        except Exception:
            logger.exception("Could not finalize bid-wizard index usage: task=%s", index_task_id)
        await engine.dispose()


class _AwaitParse(RuntimeError):
    """Document still parsing; the celery wrapper retries after a countdown."""


async def _mark_index_wait_timeout(index_task_id: str) -> dict[str, Any]:
    """解析等待重试耗尽：把任务行标 failed，避免行永久停在 pending."""
    message = "等待文档解析超时，请重试建立索引"
    session_factory, engine = _new_session_factory()
    try:
        async with session_factory() as db:
            task_row = (
                await db.execute(
                    select(BidWizardIndexTask).where(BidWizardIndexTask.id == index_task_id)
                )
            ).scalar_one_or_none()
            if (
                task_row is not None
                and task_row.status not in ("completed", "failed", "cancelled")
            ):
                task_row.status = "failed"
                task_row.error_message = message
                task_row.completed_at = utc_now()
                material_row = (
                    await db.execute(
                        select(BidWizardMaterial).where(
                            BidWizardMaterial.id == task_row.material_id
                        )
                    )
                ).scalar_one_or_none()
                if material_row is not None:
                    material_row.index_status = "failed"
                    material_row.index_error = message
                await db.commit()
        return {"status": "error", "message": message}
    finally:
        from backend.services.task_lifecycle import finalize_task_usage

        try:
            await finalize_task_usage("bid_wizard_index", index_task_id)
        except Exception:
            logger.exception("Could not finalize bid-wizard index usage: task=%s", index_task_id)
        await engine.dispose()


@celery_app.task(
    bind=True,
    name="backend.tasks.bid_wizard_tasks.run_bid_wizard_index",
    autoretry_for=(_AwaitParse,),
    retry_kwargs={"countdown": _INDEX_RETRY_COUNTDOWN, "max_retries": _INDEX_RETRY_MAX},
)
def run_bid_wizard_index(self, index_task_id: str) -> dict[str, Any]:
    """Celery entry point: LLM 分段索引一份素材."""
    try:
        return asyncio.run(_run_index(index_task_id))
    except _AwaitParse:
        if self.request.retries < _INDEX_RETRY_MAX:
            raise  # 交给 autoretry 按 countdown 重试
        return asyncio.run(_mark_index_wait_timeout(index_task_id))


# ------------------------------------------------------------------ write


async def _cancel_watcher(task_id: str, event: asyncio.Event) -> None:
    while not event.is_set():
        if is_wizard_write_cancelled(task_id):
            event.set()
            return
        await asyncio.sleep(1.0)


async def _fail_write(session_factory, task_id: str, message: str, publish: bool = False) -> dict[str, Any]:
    async with session_factory() as db:
        task = (
            await db.execute(select(BidWritingTask).where(BidWritingTask.id == task_id))
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
            await db.execute(select(BidWritingTask).where(BidWritingTask.id == task_id))
        ).scalar_one_or_none()
        if task and task.status not in {"completed"}:
            task.status = "cancelled"
            task.error_message = "用户取消了撰写任务"
            task.completed_at = utc_now()
            await db.commit()
    _publish(task_id, "status", {"status": "cancelled"})
    return {"status": "cancelled"}


async def _run_write(task_id: str) -> dict[str, Any]:
    session_factory, engine = _new_session_factory()
    cancel_event = asyncio.Event()
    watcher = asyncio.create_task(_cancel_watcher(task_id, cancel_event))
    try:
        async with session_factory() as db:
            from backend.services.task_lifecycle import claim_task_for_execution

            task = await claim_task_for_execution(
                db, task_kind="bid_wizard_write", task_id=task_id
            )
            if task is None:
                return {"status": "ignored", "message": "任务不存在、已结束或已由其他 worker 认领"}
            wizard_id = task.wizard_id
            user_id = task.user_id
            user = (
                await db.execute(select(User).where(User.id == task.user_id))
            ).scalar_one_or_none()

        _publish(task_id, "status", {"status": "running"})

        def event_callback(event_type: str, data: dict[str, Any]) -> None:
            bounded = {
                str(key)[:100]: (value[:2_000] if isinstance(value, str) else value)
                for key, value in (data or {}).items()
            }
            _publish(task_id, event_type, bounded)

        agent = BidWizardWriteAgent(
            task_id=task_id,
            workspace_dir=Path(get_settings().workspace_path) / "bid-wizard" / wizard_id / "writing" / task_id,
            session_factory=session_factory,
            event_callback=event_callback,
            cancel_event=cancel_event,
        )

        from backend.services.usage_context import (
            UsageContext,
            reset_usage_context,
            set_usage_context,
        )

        usage_identity = {
            "external_user_id": user.external_user_id if user else None,
            "local_user_id": user_id,
            "user_name": (user.username if user else user_id) or user_id,
            "enterprise_name": user.enterprise_name if user else None,
            "interior_user": bool(user.interior_user) if user else False,
        }
        usage_token = set_usage_context(
            UsageContext(**usage_identity, project_id=None, task_id=task_id, todo_id=None)
        )
        try:
            try:
                summary = await asyncio.wait_for(
                    agent.run(), timeout=BID_WIZARD_WRITE_MAX_RUNTIME_SECONDS
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError("标书撰写超过系统允许的最长执行时间") from exc
        finally:
            reset_usage_context(usage_token)

        if cancel_event.is_set() or is_wizard_write_cancelled(task_id):
            return await _mark_cancelled(session_factory, task_id)

        async with session_factory() as db:
            task = (
                await db.execute(select(BidWritingTask).where(BidWritingTask.id == task_id))
            ).scalar_one_or_none()
            if task is None:
                return {"status": "error", "message": "撰写任务不存在"}
            if task.status == "cancelled":
                return {"status": "cancelled"}
            task.status = "completed"
            task.summary = summary
            task.completed_at = utc_now()
            await db.commit()

        _publish(task_id, "result", {"status": "completed", "summary": summary or {}})
        _publish(task_id, "status", {"status": "completed"})
        return {"status": "completed", "summary": summary}
    except BidWizardCancelled:
        return await _mark_cancelled(session_factory, task_id)
    except Exception as exc:
        logger.exception("bid-wizard write task %s failed", task_id)
        return await _fail_write(session_factory, task_id, str(exc), publish=True)
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        from backend.services.task_lifecycle import finalize_task_usage

        try:
            await finalize_task_usage("bid_wizard_write", task_id)
        except Exception:
            logger.exception("Could not finalize bid-wizard write usage: task=%s", task_id)
        try:
            clear_wizard_write_cancelled(task_id)
        except Exception:
            logger.exception("Could not clear bid-wizard cancellation flag: task=%s", task_id)
        await engine.dispose()


@celery_app.task(bind=True, name="backend.tasks.bid_wizard_tasks.run_bid_wizard_write")
def run_bid_wizard_write(self, task_id: str) -> dict[str, Any]:
    """Celery entry point: 逐章撰写."""
    return asyncio.run(_run_write(task_id))


# ------------------------------------------------------------------ cleanup


@celery_app.task(name="backend.tasks.bid_wizard_tasks.cleanup_wizard_workspace")
def cleanup_wizard_workspace(wizard_id: str, user_id: str, project_id: str) -> dict[str, Any]:
    """删除项目后的异步物理清理（决策 29，不进计费）：索引/撰写产物目录 + 项目上传目录。

    NFS 上删目录可能耗时，故不放在 API 请求内；纯文件系统操作，无需 DB 会话。
    """
    import shutil

    root = Path(get_settings().workspace_path).resolve()
    targets = [
        root / "bid-wizard" / wizard_id,
        root / str(user_id) / project_id,
    ]
    removed: list[str] = []
    for target in targets:
        try:
            resolved = target.resolve()
            if root not in resolved.parents:
                logger.warning("bid-wizard cleanup skipped outside workspace: %s", resolved)
                continue
            if resolved.exists():
                shutil.rmtree(resolved, ignore_errors=True)
                removed.append(str(resolved))
        except Exception:
            logger.exception("bid-wizard cleanup failed: %s", target)
    logger.info("bid-wizard cleanup done wizard=%s removed=%s", wizard_id, removed)
    return {"wizard_id": wizard_id, "removed": removed}
