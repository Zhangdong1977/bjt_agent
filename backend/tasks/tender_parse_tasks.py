"""Celery task for 招标文件解析（tender-parse）：两段解析一条任务内完成。

骨架照 bid_wizard_tasks._run_write：claim → run → finally finalize_task_usage。
- 第一段 文档解析：复用 document_parser._parse_document_internal（docling PDF /
  markitdown docx·xlsx，产物落在 Document 行，进度照常进 Redis Stream）；
- 第二段 要素提取：tender_parse_agent.extract_tender_elements（六 Tab 行模型），
  用量经 UsageContext 记到本记录（task_id=record.id）计费结算；
- 失败重试从失败环节续跑：Document 已 parsed 时跳过第一段（需求 §5 retry 语义）；
  运行中删除记录 → 阶段间重查记录不存在即放弃后续环节（需求 §8 Q8 默认）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.agent.tender_parse_agent import extract_tender_elements, find_field_value
from backend.celery_app import celery_app
from backend.config import get_settings
from backend.models import Document, TenderParseDocument, TenderParseRecord, User
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

# 解析大 PDF（docling）+ 一次 LLM 提取的总预算；worker 侧 asyncio 先行终止，
# celery soft/hard limit 留梯度（celery_app.task_annotations）。
TENDER_PARSE_MAX_RUNTIME_SECONDS = 40 * 60
# 提取阶段单次 LLM 调用上限（对齐向导重交互 _QA_HEAVY_TIMEOUT_SECONDS），
# +60s 容纳 JSON 解析失败重试
_EXTRACT_TIMEOUT_SECONDS = 300


def _resolve_workspace_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(get_settings().workspace_path) / path
    return path


async def _load_record_context(db, record_id: str):
    """记录 + 主文档（P0 取最早一份）+ 归属用户一次取出；记录不存在（已删）返回 None。"""
    record = (
        await db.execute(select(TenderParseRecord).where(TenderParseRecord.id == record_id))
    ).scalar_one_or_none()
    if record is None:
        return None
    link = (
        await db.execute(
            select(TenderParseDocument)
            .where(TenderParseDocument.record_id == record_id)
            .order_by(TenderParseDocument.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    document = None
    if link is not None:
        document = (
            await db.execute(select(Document).where(Document.id == link.document_id))
        ).scalar_one_or_none()
    user = (
        await db.execute(select(User).where(User.id == record.user_id))
    ).scalar_one_or_none()
    return record, document, user


async def _fail_record(session_factory, record_id: str, message: str, failed_stage: str) -> dict[str, Any]:
    async with session_factory() as db:
        record = (
            await db.execute(select(TenderParseRecord).where(TenderParseRecord.id == record_id))
        ).scalar_one_or_none()
        if record is not None and record.status != "completed":
            record.status = "failed"
            record.failed_stage = failed_stage
            record.error_message = message[:2_000]
            record.completed_at = utc_now()
            if record.created_at is not None:
                record.duration_seconds = int((utc_now() - record.created_at).total_seconds())
            await db.commit()
    return {"status": "error", "stage": failed_stage, "message": message}


async def _run_tender_parse(record_id: str) -> dict[str, Any]:
    from backend.tasks.review_tasks import create_session_factory

    session_factory, engine = create_session_factory()
    # 当前所处环节：失败时据此写 failed_stage（重试从该环节续跑）
    stage_marker = "parsing"
    try:
        user: User | None = None
        async with session_factory() as db:
            from backend.services.task_lifecycle import claim_task_for_execution

            record = await claim_task_for_execution(
                db, task_kind="tender_parse", task_id=record_id
            )
            if record is None:
                return {"status": "ignored", "message": "记录不存在、已结束或已由其他 worker 认领"}
            user = (
                await db.execute(select(User).where(User.id == record.user_id))
            ).scalar_one_or_none()

        from backend.services.usage_context import (
            UsageContext,
            reset_usage_context,
            set_usage_context,
        )

        usage_token = None
        try:
            usage_token = set_usage_context(
                UsageContext(
                    external_user_id=user.external_user_id if user else None,
                    local_user_id=record.user_id,
                    user_name=(user.username if user else record.user_id) or record.user_id,
                    enterprise_name=user.enterprise_name if user else None,
                    interior_user=bool(user.interior_user) if user else False,
                    # 独立记录不挂 Project（需求 §8 Q10 默认），用量按用户/记录归集
                    project_id=None,
                    task_id=record_id,
                    todo_id=None,
                )
            )

            # ---- 第一段：文档解析（已 parsed 则跳过，供失败重试续跑） ----
            async with session_factory() as db:
                context = await _load_record_context(db, record_id)
                if context is None:
                    return {"status": "ignored", "message": "记录已被删除，放弃解析"}
                record, document, _ = context
                if document is None:
                    return await _fail_record(
                        session_factory, record_id, "关联文档缺失，请删除后重新上传", "parsing"
                    )
                record.stage = "parsing"
                record.started_at = record.started_at or utc_now()
                skip_parse = (
                    document.status == "parsed"
                    and bool(document.parsed_markdown_path)
                    and _resolve_workspace_path(document.parsed_markdown_path).exists()
                )
                if not skip_parse:
                    document.status = "parsing"
                await db.commit()

                if not skip_parse:
                    file_path = Path(document.file_path)
                    for _attempt in range(4):
                        if file_path.exists():
                            break
                        await asyncio.sleep(0.5)
                    else:
                        raise RuntimeError("文件不存在，请删除后重新上传")
                    from backend.tasks.document_parser import _parse_document_internal

                    await asyncio.wait_for(
                        _parse_document_internal(document, file_path, get_settings()),
                        timeout=TENDER_PARSE_MAX_RUNTIME_SECONDS,
                    )
                    await db.commit()

            stage_marker = "extracting"

            # 记录可能在第一段期间被删除：重查后再进入第二段
            async with session_factory() as db:
                context = await _load_record_context(db, record_id)
                if context is None:
                    return {"status": "ignored", "message": "记录已被删除，终止后续解析"}
                record, document, _ = context
                record.stage = "extracting"
                await db.commit()
                markdown = _resolve_workspace_path(
                    document.parsed_markdown_path or ""
                ).read_text(encoding="utf-8", errors="replace")
                if not markdown.strip():
                    raise RuntimeError("文档解析结果为空，无法提取招标要素")

            # ---- 第二段：AI 要素提取（六 Tab 行模型 + 原文位置） ----
            from backend.agent.bid_wizard_agent import WizardLLM

            llm = WizardLLM(timeout=_EXTRACT_TIMEOUT_SECONDS)
            result = await asyncio.wait_for(
                extract_tender_elements(llm, markdown),
                timeout=_EXTRACT_TIMEOUT_SECONDS + 60,
            )

            async with session_factory() as db:
                record = (
                    await db.execute(
                        select(TenderParseRecord).where(TenderParseRecord.id == record_id)
                    )
                ).scalar_one_or_none()
                if record is None:
                    return {"status": "ignored", "message": "记录已被删除，丢弃提取结果"}
                record.result = result
                record.manual_edit = None  # 重新解析覆盖人工修正（需求 §4.4）
                record.edited_at = None
                project_name = find_field_value(result, "basic", "项目名称").strip()
                if project_name:
                    record.project_name = project_name[:200]
                tender_unit = find_field_value(result, "basic", "招标方名称").strip()
                if tender_unit:
                    record.tender_unit = tender_unit[:200]
                record.status = "completed"
                record.failed_stage = None
                record.error_message = None
                record.completed_at = utc_now()
                if record.created_at is not None:
                    record.duration_seconds = int(
                        (record.completed_at - record.created_at).total_seconds()
                    )
                await db.commit()

            logger.info(
                "[TENDER-PARSE] completed: record=%s duration=%ss",
                record_id,
                record.duration_seconds,
            )
            return {"status": "completed", "record_id": record_id}
        finally:
            if usage_token is not None:
                try:
                    from backend.services.usage_context import reset_usage_context

                    reset_usage_context(usage_token)
                except Exception:
                    pass

    except asyncio.TimeoutError:
        logger.error("[TENDER-PARSE] timeout: record=%s stage=%s", record_id, stage_marker)
        message = (
            "AI 要素提取超时，请稍后重试"
            if stage_marker == "extracting"
            else "文档解析超时，文件过大或内容过于复杂，建议拆分后重试"
        )
        return await _fail_record(session_factory, record_id, message, stage_marker)
    except Exception as exc:
        logger.exception("[TENDER-PARSE] failed: record=%s stage=%s", record_id, stage_marker)
        return await _fail_record(session_factory, record_id, str(exc), stage_marker)
    finally:
        from backend.services.task_lifecycle import finalize_task_usage

        try:
            await finalize_task_usage("tender_parse", record_id)
        except Exception:
            logger.exception("Could not finalize tender-parse usage: record=%s", record_id)
        await engine.dispose()


@celery_app.task(bind=True, name="backend.tasks.tender_parse_tasks.run_tender_parse")
def run_tender_parse(self, record_id: str) -> dict[str, Any]:
    """Celery entry point: 上传/重试 → 文档解析 + AI 要素提取."""
    return asyncio.run(_run_tender_parse(record_id))


@celery_app.task(name="backend.tasks.tender_parse_tasks.cleanup_tender_parse_workspace")
def cleanup_tender_parse_workspace(user_id: str, record_id: str) -> dict[str, Any]:
    """删除记录后的异步物理清理（不进计费）：workspace/<user>/tender-parse/<record_id>/。

    NFS 上删目录可能耗时，不放在 API 请求内；纯文件系统操作，无需 DB 会话。
    照 cleanup_wizard_workspace 的 workspace 边界校验。
    """
    import shutil

    root = Path(get_settings().workspace_path).resolve()
    target = root / str(user_id) / "tender-parse" / record_id
    try:
        resolved = target.resolve()
        if root not in resolved.parents:
            logger.warning("tender-parse cleanup skipped outside workspace: %s", resolved)
            return {"record_id": record_id, "removed": []}
        if resolved.exists():
            shutil.rmtree(resolved, ignore_errors=True)
            logger.info("tender-parse cleanup done: %s", resolved)
            return {"record_id": record_id, "removed": [str(resolved)]}
    except Exception:
        logger.exception("tender-parse cleanup failed: %s", target)
    return {"record_id": record_id, "removed": []}
