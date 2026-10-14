"""招标文件解析（tender-parse）APIs（doc/招标文件解析需求.md §5）.

- 解析记录独立轻量表（不强绑 Project，Q10 默认）；文件本体走 documents 草稿口径
  （project_id IS NULL + owner_user_id），doc_type='tender_parse' 与检查页 'tender'
  草稿隔离、不占其配额；
- 上传即建记录并经 outbox 派发两段解析（文档解析 → AI 要素提取，parser 队列）；
- 计费：上传/重试时过 authorize_billable_task_start 闸门，倍率与向导 AI 解读
  同口径（multiplier_for_task(…, 'bid_wizard_qa')，Q5 默认）；LLM 用量在任务内
  经 UsageContext 记到记录行，finalize → settle 自动结算；
- 可见范围：本用户记录（与标书检查/向导 owner 口径一致，Q11）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from backend.api.deps import CurrentUser, DBSession
from backend.config import get_settings
from backend.models import Document, TenderParseDocument, TenderParseRecord
from backend.schemas.tender_parse import (
    TenderParseCreateResponse,
    TenderParseDocumentItem,
    TenderParseListItem,
    TenderParseListResponse,
    TenderParseManualEdit,
    TenderParseRecordResponse,
)
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tender-parse", tags=["Tender Parse"])

# 列表分页（TaskPane 窄面板）
_PAGE_SIZE_DEFAULT = 10
_PAGE_SIZE_MAX = 50


async def _owned_record(record_id: str, current_user, db) -> TenderParseRecord:
    record = (
        await db.execute(
            select(TenderParseRecord).where(
                TenderParseRecord.id == record_id,
                TenderParseRecord.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="解析记录不存在")
    return record


async def _record_documents(db, record_id: str) -> list[tuple[TenderParseDocument, Document]]:
    rows = (
        await db.execute(
            select(TenderParseDocument, Document)
            .join(Document, Document.id == TenderParseDocument.document_id)
            .where(TenderParseDocument.record_id == record_id)
            .order_by(TenderParseDocument.created_at.asc())
        )
    ).all()
    return [(link, doc) for link, doc in rows]


def _document_item(document: Document) -> TenderParseDocumentItem:
    file_size: int | None = None
    try:
        file_size = os.path.getsize(document.file_path)
    except OSError:
        file_size = None
    return TenderParseDocumentItem(
        document_id=document.id,
        original_filename=document.original_filename,
        file_size=file_size,
        page_count=document.page_count,
        word_count=document.word_count,
        doc_status=document.status,
    )


async def _record_response(db, record: TenderParseRecord) -> TenderParseRecordResponse:
    documents = await _record_documents(db, record.id)
    return TenderParseRecordResponse(
        id=record.id,
        project_name=record.project_name,
        tender_unit=record.tender_unit,
        status=record.status,
        stage=record.stage,
        failed_stage=record.failed_stage,
        error_message=record.error_message,
        duration_seconds=record.duration_seconds,
        created_at=record.created_at,
        updated_at=record.updated_at,
        result=record.result,
        manual_edit=record.manual_edit,
        edited_at=record.edited_at,
        documents=[_document_item(doc) for _link, doc in documents],
    )


def _billing_multiplier(sales_config) -> Any:
    from backend.services.sales import multiplier_for_task

    # 与向导 AI 解读同口径计费（需求 §8 Q5 默认）：无独立配置键，复用 bid_wizard_qa 倍率
    return multiplier_for_task(sales_config, "bid_wizard_qa")


@router.get("/records", response_model=TenderParseListResponse)
async def list_records(
    db: DBSession,
    current_user: CurrentUser,
    project_name: str | None = Query(default=None, max_length=200),
    tender_unit: str | None = Query(default=None, max_length=200),
    record_status: str | None = Query(default=None, alias="status", max_length=20),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_PAGE_SIZE_DEFAULT, ge=1, le=_PAGE_SIZE_MAX),
) -> TenderParseListResponse:
    """解析记录列表（创建时间倒序）；status=running 合并 pending+running（解析中）."""
    conditions = [TenderParseRecord.user_id == current_user.id]
    if project_name and project_name.strip():
        conditions.append(TenderParseRecord.project_name.ilike(f"%{project_name.strip()}%"))
    if tender_unit and tender_unit.strip():
        conditions.append(TenderParseRecord.tender_unit.ilike(f"%{tender_unit.strip()}%"))
    if record_status == "running":
        conditions.append(TenderParseRecord.status.in_(("pending", "running")))
    elif record_status in ("pending", "completed", "failed"):
        conditions.append(TenderParseRecord.status == record_status)

    total = (
        await db.execute(select(func.count(TenderParseRecord.id)).where(*conditions))
    ).scalar_one()

    records = (
        await db.execute(
            select(TenderParseRecord)
            .where(*conditions)
            .order_by(TenderParseRecord.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    items: list[TenderParseListItem] = []
    if records:
        record_ids = [r.id for r in records]
        doc_rows = (
            await db.execute(
                select(TenderParseDocument.record_id, Document)
                .join(Document, Document.id == TenderParseDocument.document_id)
                .where(TenderParseDocument.record_id.in_(record_ids))
                .order_by(TenderParseDocument.created_at.asc())
            )
        ).all()
        first_doc: dict[str, Document] = {}
        for record_id, document in doc_rows:
            first_doc.setdefault(record_id, document)
        for record in records:
            document = first_doc.get(record.id)
            items.append(
                TenderParseListItem(
                    id=record.id,
                    project_name=record.project_name,
                    tender_unit=record.tender_unit,
                    status=record.status,
                    stage=record.stage,
                    failed_stage=record.failed_stage,
                    error_message=record.error_message,
                    duration_seconds=record.duration_seconds,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    file_name=document.original_filename if document else None,
                    file_size=None,
                )
            )
    return TenderParseListResponse(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.post(
    "/records", response_model=TenderParseCreateResponse, status_code=status.HTTP_201_CREATED
)
async def create_record(
    db: DBSession,
    current_user: CurrentUser,
    file: UploadFile = File(...),
) -> TenderParseCreateResponse:
    """上传一份招标文件并创建解析记录（pdf/docx/doc/xlsx，同标书检查口径）。"""
    from backend.api.documents import (
        _decode_rfc2047_filename,
        _save_upload_file,
        _validate_upload_file,
    )
    from backend.services.sales import multiplier_for_task
    from backend.services.task_lifecycle import (
        add_task_dispatch,
        authorize_billable_task_start,
        dispatch_task_outbox,
    )

    # 客户端素材经 VSTO 插件（.NET Framework）转发上传，中文名到这里是 RFC 2047 编码串
    file.filename = _decode_rfc2047_filename(file.filename)
    _validate_upload_file(file)

    sales_config = await authorize_billable_task_start(
        db, user_id=current_user.id, operation_name="招标文件解析"
    )
    stem = Path(file.filename or "").stem.strip()[:200] or "未命名招标文件"
    record = TenderParseRecord(
        user_id=current_user.id,
        project_name=stem,
        status="pending",
        stage="queued",
        billing_multiplier=multiplier_for_task(sales_config, "bid_wizard_qa"),
        billing_status="pending",
    )
    db.add(record)
    await db.flush()

    doc_dir = get_settings().workspace_path / str(current_user.id) / "tender-parse" / record.id
    file_path = Path(await _save_upload_file(file, doc_dir, fsync=False))
    document = Document(
        project_id=None,
        owner_user_id=current_user.id,
        source="web",
        doc_type="tender_parse",
        original_filename=file.filename,
        file_path=str(file_path),
        status="pending",
    )
    db.add(document)
    await db.flush()
    db.add(TenderParseDocument(record_id=record.id, document_id=document.id))
    outbox = add_task_dispatch(db, task_kind="tender_parse", task_id=record.id)
    await db.flush()
    await db.commit()
    await db.refresh(record)

    await dispatch_task_outbox(outbox.id)
    return TenderParseCreateResponse(record=await _record_response(db, record))


@router.get("/records/{record_id}", response_model=TenderParseRecordResponse)
async def get_record(record_id: str, db: DBSession, current_user: CurrentUser) -> TenderParseRecordResponse:
    record = await _owned_record(record_id, current_user, db)
    return await _record_response(db, record)


@router.put("/records/{record_id}", response_model=TenderParseRecordResponse)
async def save_manual_edit(
    record_id: str,
    payload: TenderParseManualEdit,
    db: DBSession,
    current_user: CurrentUser,
) -> TenderParseRecordResponse:
    """保存人工修正版（需求 §4.4）：整包覆盖 manual_edit，展示以修正版为准。"""
    record = await _owned_record(record_id, current_user, db)
    if record.status != "completed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="解析完成后才能编辑保存")
    record.manual_edit = payload.as_plain_dict()
    record.edited_at = utc_now()
    await db.commit()
    await db.refresh(record)
    return await _record_response(db, record)


@router.delete("/records/{record_id}", status_code=status.HTTP_200_OK)
async def delete_record(record_id: str, db: DBSession, current_user: CurrentUser) -> dict[str, Any]:
    """删除解析记录及关联文档；解析中的记录删除后任务在阶段间自动放弃（Q8 默认）."""
    from backend.services.task_lifecycle import cancel_pending_dispatch
    from backend.tasks.tender_parse_tasks import cleanup_tender_parse_workspace

    record = await _owned_record(record_id, current_user, db)
    # 未派发的 outbox 取消；已派发任务靠「记录不存在即放弃」兜底
    cancel_pending_dispatch(db, task_kind="tender_parse", task_id=record.id)
    for _link, document in await _record_documents(db, record.id):
        await db.delete(document)  # link 行由 FK CASCADE 级联删除
    await db.delete(record)
    await db.commit()
    cleanup_tender_parse_workspace.delay(current_user.id, record.id)
    return {"deleted": True, "id": record_id}


@router.post("/records/{record_id}/retry", response_model=TenderParseRecordResponse)
async def retry_record(record_id: str, db: DBSession, current_user: CurrentUser) -> TenderParseRecordResponse:
    """失败重试（从失败环节续跑）或已完成后重新解析（覆盖旧结果，Q12 手动触发）."""
    from backend.services.sales import multiplier_for_task
    from backend.services.task_lifecycle import (
        add_task_dispatch,
        authorize_billable_task_start,
        dispatch_task_outbox,
    )

    record = await _owned_record(record_id, current_user, db)
    if record.status in ("pending", "running"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该记录正在解析中")

    # 重新发起会再次调用 LLM（失败续跑也可能到提取环节）→ 重新过计费闸门
    sales_config = await authorize_billable_task_start(
        db, user_id=current_user.id, operation_name="招标文件解析"
    )
    record.status = "pending"
    record.stage = "queued"
    record.failed_stage = None
    record.error_message = None
    record.completed_at = None
    record.duration_seconds = None
    record.manual_edit = None  # 重新解析覆盖人工修正（需求 §4.4）
    record.edited_at = None
    record.billing_multiplier = multiplier_for_task(sales_config, "bid_wizard_qa")
    record.billing_status = "pending"
    record.billing_error = None
    record.billing_attempts = 0
    record.usage_finalized_at = None
    record.billing_settled_at = None
    outbox = add_task_dispatch(db, task_kind="tender_parse", task_id=record.id)
    await db.flush()
    await db.commit()
    await db.refresh(record)

    await dispatch_task_outbox(outbox.id)
    return await _record_response(db, record)


@router.get("/records/{record_id}/file")
async def download_record_file(
    record_id: str, db: DBSession, current_user: CurrentUser
) -> FileResponse:
    """下载记录的原始招标文件（Q7 默认：点击即下载）."""
    record = await _owned_record(record_id, current_user, db)
    documents = await _record_documents(db, record.id)
    if not documents:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="原始文件不存在")
    document = documents[0][1]
    path = Path(document.file_path)
    if not path.is_absolute():
        path = Path(get_settings().workspace_path) / path
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="原始文件已丢失，请重新上传")
    # media_type 留空让 FileResponse 按文件名后缀推断，避免 doc/docx MIME 误判
    return FileResponse(path, filename=document.original_filename)
