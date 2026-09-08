"""AI编标（bid-wizard）APIs：四阶段向导（素材准备→需求确认→编写大纲→逐章撰写）.

与 V1 bid-draft 完全独立的模块（doc/workspace/20-bid-wizard.md §5.2）：
- 素材/招标文件 multipart 直传（插件与页面共用），material 上传即自动建索引任务（决策 17a）；
- 问卷生成 / Spec 生成与修订 = 同步微任务（bid_wizard_qa，请求内执行、结束即结算）；
- 撰写任务 = outbox → generation 队列（bid_wizard_write）；written 由页面写入 Word 后回报。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from backend.api.bid_draft import _node_sort_key
from backend.api.deps import CurrentUser, DBSession
from backend.config import get_settings
from backend.models import (
    BidWizard,
    BidWizardIndexTask,
    BidWizardMaterial,
    BidWizardQaTask,
    BidWizardSection,
    BidWizardWhitelist,
    BidWritingTask,
    Document,
    Project,
)
from backend.schemas.bid_wizard import (
    QuestionnaireAnswer,
    RequirementsUpdate,
    SectionWrittenResponse,
    SpecRevise,
    SpecUpdate,
    WizardAccessResponse,
    WizardCreate,
    WizardEstimateResponse,
    WizardMaterialIndexResponse,
    WizardMaterialResponse,
    WizardResponse,
    WizardSectionContentResponse,
    WizardSectionResponse,
    WizardStageUpdate,
    WritingTaskCreate,
    WritingTaskResponse,
)
from backend.services.sse_service import sse_manager
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bid-wizard", tags=["Bid Wizard"])

_STAGE_ORDER = {"material": 0, "requirement": 1, "outline": 2, "writing": 3}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_QA_TIMEOUT_SECONDS = 120


# ------------------------------------------------------------------ helpers


async def _require_access(db: DBSession, current_user) -> None:
    """Feature gate: enabled / whitelist / disabled (env BID_WIZARD_ACCESS_MODE)."""
    mode = (get_settings().bid_wizard_access_mode or "disabled").strip().lower()
    if mode == "enabled":
        return
    if mode == "whitelist":
        row = (
            await db.execute(
                select(BidWizardWhitelist).where(BidWizardWhitelist.user_id == current_user.id)
            )
        ).scalar_one_or_none()
        if row is not None:
            return
    raise HTTPException(status_code=403, detail="AI编标功能尚未开放，敬请期待")


async def _owned_wizard(wizard_id: str, current_user, db: DBSession) -> BidWizard:
    wizard = (
        await db.execute(select(BidWizard).where(BidWizard.id == wizard_id))
    ).scalar_one_or_none()
    if wizard is None or wizard.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="向导不存在或无权访问")
    return wizard


def _resolve_workspace_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(get_settings().workspace_path) / path
    return path


def _mark_downstream_stale(wizard: BidWizard) -> None:
    """素材/招标文件变更 → 需求与 Spec 产物标记过期（决策 19：不自动级联）。"""
    if wizard.questionnaire or wizard.requirements:
        wizard.requirements_stale = True
    if wizard.spec:
        wizard.spec_stale = True


async def _wizard_tender_document(db: DBSession, wizard: BidWizard) -> Document | None:
    return (
        await db.execute(
            select(Document).where(
                Document.project_id == wizard.project_id,
                Document.doc_type == "tender",
            )
        )
    ).scalar_one_or_none()


async def _store_wizard_document(
    db: DBSession, wizard: BidWizard, current_user, file: UploadFile, doc_type: str
) -> Document:
    """Store the upload under workspace/<user>/<project>/<doc_type>/（不提交，由调用方统一 commit）."""
    from backend.api.documents import _save_upload_file, _validate_upload_file

    _validate_upload_file(file)
    doc_dir = get_settings().workspace_path / str(current_user.id) / wizard.project_id / doc_type
    file_path = Path(await _save_upload_file(file, doc_dir, fsync=False))
    document = Document(
        project_id=wizard.project_id,
        doc_type=doc_type,
        original_filename=file.filename,
        file_path=str(file_path),
        status="pending",
    )
    db.add(document)
    await db.flush()
    return document


def _material_response(material: BidWizardMaterial, document: Document | None) -> WizardMaterialResponse:
    return WizardMaterialResponse(
        id=material.id,
        document_id=material.document_id,
        category=material.category,
        index_status=material.index_status,
        indexed_at=material.indexed_at,
        index_error=material.index_error,
        chunk_count=material.chunk_count,
        original_filename=document.original_filename if document else None,
        doc_status=document.status if document else None,
        word_count=getattr(document, "word_count", None) if document else None,
        page_count=getattr(document, "page_count", None) if document else None,
        file_size=getattr(document, "file_size", None) if document else None,
        created_at=material.created_at,
    )


async def _run_qa_task(
    db: DBSession,
    wizard: BidWizard,
    current_user,
    *,
    action: str,
    runner: Callable[[], Awaitable[Any]],
) -> Any:
    """Synchronous micro task: authorize → row → UsageContext → LLM → finalize（结束即结算）."""
    from backend.services.sales import multiplier_for_task
    from backend.services.task_lifecycle import (
        authorize_billable_task_start,
        finalize_task_usage,
    )

    sales_config = await authorize_billable_task_start(
        db, user_id=current_user.id, operation_name="AI编标问答"
    )
    qa = BidWizardQaTask(
        wizard_id=wizard.id,
        user_id=current_user.id,
        action=action,
        status="running",
        billing_multiplier=multiplier_for_task(sales_config, "bid_wizard_qa"),
        billing_status="pending",
    )
    db.add(qa)
    await db.commit()
    await db.refresh(qa)

    from backend.services.usage_context import (
        UsageContext,
        reset_usage_context,
        set_usage_context,
    )

    usage_token = set_usage_context(
        UsageContext(
            external_user_id=current_user.external_user_id,
            local_user_id=current_user.id,
            user_name=current_user.username or current_user.id,
            enterprise_name=current_user.enterprise_name,
            interior_user=bool(current_user.interior_user),
            project_id=None,
            task_id=qa.id,
            todo_id=None,
        )
    )
    result: Any = None
    try:
        try:
            result = await asyncio.wait_for(runner(), timeout=_QA_TIMEOUT_SECONDS)
            qa.status = "completed"
        except HTTPException as exc:
            qa.status = "failed"
            qa.error_message = str(exc.detail)[:2_000]
            raise
        except asyncio.TimeoutError as exc:
            qa.status = "failed"
            qa.error_message = "AI 处理超时"
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="AI 处理超时，请稍后重试",
            ) from exc
        except Exception as exc:
            qa.status = "failed"
            qa.error_message = str(exc)[:2_000]
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"AI 处理失败：{str(exc)[:300]}",
            ) from exc
    finally:
        reset_usage_context(usage_token)
        try:
            await db.commit()
        except Exception:
            logger.exception("bid-wizard qa status commit failed: %s", qa.id)
        try:
            await finalize_task_usage("bid_wizard_qa", qa.id)
        except Exception:
            logger.exception("bid-wizard qa finalize failed: %s", qa.id)
    return result


# ------------------------------------------------------------------ access


@router.get("/access", response_model=WizardAccessResponse)
async def get_access(db: DBSession, current_user: CurrentUser) -> WizardAccessResponse:
    mode = (get_settings().bid_wizard_access_mode or "disabled").strip().lower()
    if mode not in ("enabled", "whitelist", "disabled"):
        mode = "disabled"
    enabled = mode == "enabled"
    if mode == "whitelist":
        row = (
            await db.execute(
                select(BidWizardWhitelist).where(BidWizardWhitelist.user_id == current_user.id)
            )
        ).scalar_one_or_none()
        enabled = row is not None
    return WizardAccessResponse(enabled=enabled, mode=mode)


@router.get("/estimate", response_model=WizardEstimateResponse)
async def estimate_index_cost(bytes_size: int = Query(..., ge=1, le=2_147_483_647)) -> WizardEstimateResponse:
    """上传前预估：由文件字节数粗估正文字数与 token 量级（决策 17a 的确认弹窗用）。

    office/pdf 是压缩容器，按 ~0.4 的文本占比粗估；只作量级提示，
    实际计费按索引任务的真实用量结算。
    """
    chars = max(1_000, int(bytes_size * 0.4))
    from backend.agent.bid_wizard_agent import estimate_tokens

    return WizardEstimateResponse(chars=chars, estimated_tokens=estimate_tokens(chars))


# ------------------------------------------------------------------ wizard


@router.post("/wizards", response_model=WizardResponse, status_code=status.HTTP_201_CREATED)
async def create_wizard(
    body: WizardCreate,
    db: DBSession,
    current_user: CurrentUser,
) -> BidWizard:
    await _require_access(db, current_user)
    if body.project_id:
        project = (
            await db.execute(select(Project).where(Project.id == body.project_id))
        ).scalar_one_or_none()
        if project is None or project.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="项目不存在或无权访问")
        if project.project_type != "bid_wizard":
            raise HTTPException(status_code=400, detail="请选择 AI编标 项目，或留空由系统新建")
    else:
        project = Project(
            user_id=current_user.id,
            name=(body.project_name or f"AI编标 {datetime.now().strftime('%Y-%m-%d')}").strip()[:200],
            project_type="bid_wizard",
            duplicate_mode="pair",
        )
        db.add(project)
        await db.flush()

    existing = (
        await db.execute(
            select(BidWizard).where(
                BidWizard.project_id == project.id, BidWizard.status == "active"
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    wizard = BidWizard(
        user_id=current_user.id,
        project_id=project.id,
        stage="material",
        status="active",
    )
    db.add(wizard)
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.get("/wizards/active", response_model=WizardResponse)
async def get_active_wizard(db: DBSession, current_user: CurrentUser) -> BidWizard:
    """入口恢复：当前用户最近一个活动向导（跨项目，断点续作）。"""
    wizard = (
        await db.execute(
            select(BidWizard)
            .where(BidWizard.user_id == current_user.id, BidWizard.status == "active")
            .order_by(BidWizard.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if wizard is None:
        raise HTTPException(status_code=404, detail="当前没有进行中的 AI编标 向导")
    return wizard


@router.get("/wizards/{wizard_id}", response_model=WizardResponse)
async def get_wizard(wizard_id: str, db: DBSession, current_user: CurrentUser) -> BidWizard:
    return await _owned_wizard(wizard_id, current_user, db)


@router.post("/wizards/{wizard_id}/stage", response_model=WizardResponse)
async def update_wizard_stage(
    wizard_id: str, body: WizardStageUpdate, db: DBSession, current_user: CurrentUser
) -> BidWizard:
    wizard = await _owned_wizard(wizard_id, current_user, db)
    if wizard.status != "active":
        raise HTTPException(status_code=400, detail="向导已结束，不能调整阶段")
    target = body.stage
    if _STAGE_ORDER[target] == _STAGE_ORDER[wizard.stage]:
        return wizard
    if _STAGE_ORDER[target] < _STAGE_ORDER[wizard.stage]:
        # 回退：下游产物标记过期（决策 19）
        if _STAGE_ORDER[target] <= _STAGE_ORDER["material"]:
            _mark_downstream_stale(wizard)
        elif _STAGE_ORDER[target] <= _STAGE_ORDER["requirement"]:
            if wizard.spec:
                wizard.spec_stale = True
    if target == "writing" and not wizard.spec_confirmed_at:
        raise HTTPException(status_code=400, detail="请先在「编写大纲」阶段确认大纲后再进入撰写")
    wizard.stage = target
    await db.commit()
    await db.refresh(wizard)
    return wizard


# ------------------------------------------------------------------ 素材准备


@router.post(
    "/wizards/{wizard_id}/tender", response_model=WizardMaterialResponse, status_code=status.HTTP_201_CREATED
)
async def upload_tender(
    wizard_id: str,
    db: DBSession,
    current_user: CurrentUser,
    file: UploadFile = File(...),
) -> WizardMaterialResponse:
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _require_access(db, current_user)
    existing = await _wizard_tender_document(db, wizard)
    if existing is not None:
        raise HTTPException(status_code=400, detail="招标文件已存在，请先删除后重新上传")
    document = await _store_wizard_document(db, wizard, current_user, file, doc_type="tender")
    _mark_downstream_stale(wizard)
    await db.commit()
    await db.refresh(document)
    from backend.tasks.document_parser import parse_document

    parse_document.delay(document.id)
    return WizardMaterialResponse(
        id=f"tender:{document.id}",
        document_id=document.id,
        category="tender",
        index_status="pending",
        indexed_at=None,
        index_error=None,
        chunk_count=None,
        original_filename=document.original_filename,
        doc_status=document.status,
        word_count=None,
        page_count=None,
        file_size=None,
        created_at=document.created_at,
    )


@router.delete("/wizards/{wizard_id}/tender", response_model=WizardResponse)
async def delete_tender(wizard_id: str, db: DBSession, current_user: CurrentUser) -> BidWizard:
    wizard = await _owned_wizard(wizard_id, current_user, db)
    document = await _wizard_tender_document(db, wizard)
    if document is not None:
        await db.delete(document)
    _mark_downstream_stale(wizard)
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.post(
    "/wizards/{wizard_id}/materials", response_model=WizardMaterialResponse, status_code=status.HTTP_201_CREATED
)
async def upload_material(
    wizard_id: str,
    db: DBSession,
    current_user: CurrentUser,
    file: UploadFile = File(...),
    category: str | None = Query(default=None, max_length=100),
) -> BidWizardMaterial:
    """上传素材：解析 + 自动 LLM 分段索引（决策 17a，上传即索引计费）。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _require_access(db, current_user)

    from backend.services.sales import multiplier_for_task
    from backend.services.task_lifecycle import (
        add_task_dispatch,
        authorize_billable_task_start,
        dispatch_task_outbox,
    )

    count = (
        await db.execute(
            select(BidWizardMaterial).where(BidWizardMaterial.wizard_id == wizard.id)
        )
    ).scalars()
    existing_count = len(list(count))
    if existing_count >= get_settings().bid_wizard_material_max_count:
        raise HTTPException(
            status_code=400,
            detail=f"素材数量已达上限（{get_settings().bid_wizard_material_max_count} 份）",
        )

    # 素材常批量上传：索引任务用更高的并发上限，避免"传第二份被 409"（余额检查仍生效）。
    sales_config = await authorize_billable_task_start(
        db,
        user_id=current_user.id,
        operation_name="AI编标素材索引",
        max_active_tasks=get_settings().bid_wizard_index_max_active_tasks,
    )
    document = await _store_wizard_document(db, wizard, current_user, file, doc_type="material")
    material = BidWizardMaterial(
        wizard_id=wizard.id,
        document_id=document.id,
        user_id=current_user.id,
        category=(category or "").strip() or None,
        index_status="pending",
    )
    db.add(material)
    await db.flush()
    index_task = BidWizardIndexTask(
        wizard_id=wizard.id,
        material_id=material.id,
        user_id=current_user.id,
        status="pending",
        billing_multiplier=multiplier_for_task(sales_config, "bid_wizard_index"),
        billing_status="pending",
    )
    db.add(index_task)
    await db.flush()
    outbox = add_task_dispatch(db, task_kind="bid_wizard_index", task_id=index_task.id)
    await db.flush()
    index_task.celery_task_id = outbox.celery_task_id
    _mark_downstream_stale(wizard)
    await db.commit()
    await db.refresh(material)
    await db.refresh(document)
    from backend.tasks.document_parser import parse_document

    parse_document.delay(document.id)
    await dispatch_task_outbox(outbox.id)
    return _material_response(material, document)


@router.get("/wizards/{wizard_id}/materials", response_model=list[WizardMaterialResponse])
async def list_materials(
    wizard_id: str, db: DBSession, current_user: CurrentUser
) -> list[WizardMaterialResponse]:
    wizard = await _owned_wizard(wizard_id, current_user, db)
    rows = (
        await db.execute(
            select(BidWizardMaterial, Document)
            .join(Document, Document.id == BidWizardMaterial.document_id)
            .where(BidWizardMaterial.wizard_id == wizard.id)
            .order_by(BidWizardMaterial.created_at.asc())
        )
    ).all()
    result: list[WizardMaterialResponse] = []
    for material, document in rows:
        result.append(
            WizardMaterialResponse(
                id=material.id,
                document_id=material.document_id,
                category=material.category,
                index_status=material.index_status,
                indexed_at=material.indexed_at,
                index_error=material.index_error,
                chunk_count=material.chunk_count,
                original_filename=document.original_filename,
                doc_status=document.status,
                word_count=getattr(document, "word_count", None),
                page_count=getattr(document, "page_count", None),
                file_size=getattr(document, "file_size", None),
                created_at=material.created_at,
            )
        )
    return result


@router.delete("/wizards/{wizard_id}/materials/{document_id}", response_model=WizardResponse)
async def delete_material(
    wizard_id: str, document_id: str, db: DBSession, current_user: CurrentUser
) -> BidWizard:
    wizard = await _owned_wizard(wizard_id, current_user, db)
    material = (
        await db.execute(
            select(BidWizardMaterial).where(
                BidWizardMaterial.wizard_id == wizard.id,
                BidWizardMaterial.document_id == document_id,
            )
        )
    ).scalar_one_or_none()
    if material is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    document = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if document is not None:
        await db.delete(document)  # material 行由 FK CASCADE 级联删除
    # 索引产物目录留着无害；如需清理也可删除（workspace 路径按 document_id 隔离）
    from backend.agent.bid_wizard_agent import material_dir

    try:
        import shutil

        shutil.rmtree(material_dir(get_settings().workspace_path, wizard.id, document_id), ignore_errors=True)
    except Exception:
        pass
    _mark_downstream_stale(wizard)
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.get(
    "/wizards/{wizard_id}/materials/{document_id}/index",
    response_model=WizardMaterialIndexResponse,
)
async def get_material_index(
    wizard_id: str, document_id: str, db: DBSession, current_user: CurrentUser
) -> WizardMaterialIndexResponse:
    wizard = await _owned_wizard(wizard_id, current_user, db)
    material = (
        await db.execute(
            select(BidWizardMaterial).where(
                BidWizardMaterial.wizard_id == wizard.id,
                BidWizardMaterial.document_id == document_id,
            )
        )
    ).scalar_one_or_none()
    if material is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    from backend.agent.bid_wizard_agent import material_dir

    index_path = material_dir(get_settings().workspace_path, wizard.id, document_id) / "index.md"
    content = None
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8", errors="replace")[:50_000]
    return WizardMaterialIndexResponse(
        material_id=material.id,
        index_status=material.index_status,
        chunk_count=material.chunk_count,
        index_content=content,
    )


@router.post(
    "/wizards/{wizard_id}/materials/{document_id}/reindex",
    response_model=WizardMaterialResponse,
)
async def reindex_material(
    wizard_id: str, document_id: str, db: DBSession, current_user: CurrentUser
) -> WizardMaterialResponse:
    """索引失败重试：新建一个索引任务（原任务已终态，不复活）。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _require_access(db, current_user)
    material = (
        await db.execute(
            select(BidWizardMaterial).where(
                BidWizardMaterial.wizard_id == wizard.id,
                BidWizardMaterial.document_id == document_id,
            )
        )
    ).scalar_one_or_none()
    if material is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    if material.index_status == "indexing":
        raise HTTPException(status_code=409, detail="素材正在索引中，请稍候")
    document = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if document is None or document.status != "parsed":
        raise HTTPException(status_code=409, detail="素材尚未解析完成，请稍候")

    from backend.services.sales import multiplier_for_task
    from backend.services.task_lifecycle import (
        add_task_dispatch,
        authorize_billable_task_start,
        dispatch_task_outbox,
    )

    sales_config = await authorize_billable_task_start(
        db,
        user_id=current_user.id,
        operation_name="AI编标素材索引",
        max_active_tasks=get_settings().bid_wizard_index_max_active_tasks,
    )
    material.index_status = "pending"
    material.index_error = None
    index_task = BidWizardIndexTask(
        wizard_id=wizard.id,
        material_id=material.id,
        user_id=current_user.id,
        status="pending",
        billing_multiplier=multiplier_for_task(sales_config, "bid_wizard_index"),
        billing_status="pending",
    )
    db.add(index_task)
    await db.flush()
    outbox = add_task_dispatch(db, task_kind="bid_wizard_index", task_id=index_task.id)
    await db.flush()
    index_task.celery_task_id = outbox.celery_task_id
    await db.commit()
    await db.refresh(material)
    await dispatch_task_outbox(outbox.id)
    document = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one()
    return _material_response(material, document)


# ------------------------------------------------------------------ 需求确认


@router.post("/wizards/{wizard_id}/questionnaire", response_model=WizardResponse)
async def generate_questionnaire(
    wizard_id: str, db: DBSession, current_user: CurrentUser
) -> BidWizard:
    """同步微任务：确保招标要素 → 生成结构化问卷（含素材依据建议答案）。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _require_access(db, current_user)
    document = await _wizard_tender_document(db, wizard)
    if document is None:
        raise HTTPException(status_code=400, detail="请先上传招标文件")
    if document.status != "parsed" or not document.parsed_markdown_path:
        raise HTTPException(status_code=409, detail="招标文件尚未解析完成，请稍候")

    wizard_id_ref = wizard.id
    analysis_ref = wizard.analysis if isinstance(wizard.analysis, dict) and wizard.analysis else None
    markdown_path = document.parsed_markdown_path

    async def runner() -> dict[str, Any]:
        from backend.agent.bid_wizard_agent import (
            WizardLLM,
            analyze_tender,
            build_material_index_text,
            generate_questionnaire as gen_q,
            _load_material_index_rows,
        )

        llm = WizardLLM(timeout=_QA_TIMEOUT_SECONDS)
        analysis = analysis_ref
        if analysis is None:
            markdown = _resolve_workspace_path(markdown_path).read_text(
                encoding="utf-8", errors="replace"
            )
            analysis = await analyze_tender(llm, markdown)
        material_entries = await _load_material_index_rows(db, wizard_id_ref)
        return {
            "analysis": analysis,
            "questionnaire": await gen_q(
                llm,
                analysis=analysis,
                material_index_text=build_material_index_text(material_entries),
            ),
        }

    payload = await _run_qa_task(db, wizard, current_user, action="questionnaire", runner=runner)
    wizard.analysis = payload["analysis"]
    wizard.questionnaire = payload["questionnaire"]
    # 重新生成问卷 = 需求基于当前素材；旧作答作废、Spec 需重生成
    wizard.requirements = None
    wizard.requirements_stale = False
    if wizard.spec:
        wizard.spec_stale = True
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.put("/wizards/{wizard_id}/requirements", response_model=WizardResponse)
async def save_requirements(
    wizard_id: str, body: RequirementsUpdate, db: DBSession, current_user: CurrentUser
) -> BidWizard:
    wizard = await _owned_wizard(wizard_id, current_user, db)
    if not isinstance(wizard.questionnaire, dict) or not wizard.questionnaire.get("questions"):
        raise HTTPException(status_code=400, detail="请先生成问卷")
    from backend.agent.bid_wizard_agent import merge_questionnaire_answers

    merged = merge_questionnaire_answers(
        wizard.questionnaire, [answer.model_dump() for answer in body.answers]
    )
    wizard.requirements = merged
    wizard.requirements_stale = False
    # 答案变了 → 基于旧答案的 Spec 过期（决策 19）
    if wizard.spec:
        wizard.spec_stale = True
    await db.commit()
    await db.refresh(wizard)
    return wizard


# ------------------------------------------------------------------ 编写大纲


@router.post("/wizards/{wizard_id}/spec", response_model=WizardResponse)
async def generate_spec(wizard_id: str, db: DBSession, current_user: CurrentUser) -> BidWizard:
    """同步微任务：编写需求 + 招标要素 + 素材索引 → Spec（目录/摘要/图表规划）。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _require_access(db, current_user)
    if wizard.requirements is None:
        raise HTTPException(status_code=400, detail="请先完成需求确认（保存问卷答案）")

    wizard_ref = wizard

    async def runner() -> list[dict[str, Any]]:
        from backend.agent.bid_wizard_agent import (
            WizardLLM,
            _load_material_index_rows,
            build_material_index_text,
            build_requirements_text,
            generate_spec as gen_spec,
        )

        llm = WizardLLM(timeout=_QA_TIMEOUT_SECONDS)
        material_entries = await _load_material_index_rows(db, wizard_ref.id)
        return await gen_spec(
            llm,
            analysis=wizard_ref.analysis if isinstance(wizard_ref.analysis, dict) else {},
            requirements_text=build_requirements_text(wizard_ref.requirements),
            material_index_text=build_material_index_text(material_entries),
        )

    spec = await _run_qa_task(db, wizard, current_user, action="spec_generate", runner=runner)
    wizard.spec = spec
    wizard.spec_previous = None
    wizard.spec_stale = False
    wizard.spec_confirmed_at = None
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.put("/wizards/{wizard_id}/spec", response_model=WizardResponse)
async def save_spec(
    wizard_id: str, body: SpecUpdate, db: DBSession, current_user: CurrentUser
) -> BidWizard:
    """手工编辑保存：规范化编号/钳制参数；确认状态重置。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    from backend.agent.bid_wizard_agent import normalize_spec

    spec = normalize_spec([node.model_dump() for node in body.spec])
    if not spec:
        raise HTTPException(status_code=422, detail="编写大纲不能为空")
    wizard.spec = spec
    wizard.spec_stale = False
    wizard.spec_confirmed_at = None
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.post("/wizards/{wizard_id}/spec/revise", response_model=WizardResponse)
async def revise_spec(
    wizard_id: str, body: SpecRevise, db: DBSession, current_user: CurrentUser
) -> BidWizard:
    """同步微任务：自然语言指令让 AI 修订 Spec（旧版存 spec_previous 供一步回退）。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _require_access(db, current_user)
    if not isinstance(wizard.spec, list) or not wizard.spec:
        raise HTTPException(status_code=400, detail="请先生成编写大纲")

    current_spec = wizard.spec
    instruction = body.instruction

    async def runner() -> list[dict[str, Any]]:
        from backend.agent.bid_wizard_agent import WizardLLM, revise_spec as rev_spec

        llm = WizardLLM(timeout=_QA_TIMEOUT_SECONDS)
        return await rev_spec(llm, current_spec=current_spec, instruction=instruction)

    spec = await _run_qa_task(db, wizard, current_user, action="spec_revise", runner=runner)
    wizard.spec_previous = wizard.spec
    wizard.spec = spec
    wizard.spec_stale = False
    wizard.spec_confirmed_at = None
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.post("/wizards/{wizard_id}/spec/rollback", response_model=WizardResponse)
async def rollback_spec(wizard_id: str, db: DBSession, current_user: CurrentUser) -> BidWizard:
    """AI 修订的一步回退（无 LLM、不计费）。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    if not isinstance(wizard.spec_previous, list) or not wizard.spec_previous:
        raise HTTPException(status_code=404, detail="没有可回退的大纲版本")
    wizard.spec, wizard.spec_previous = wizard.spec_previous, wizard.spec
    wizard.spec_confirmed_at = None
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.post("/wizards/{wizard_id}/spec/confirm", response_model=WizardResponse)
async def confirm_spec(wizard_id: str, db: DBSession, current_user: CurrentUser) -> BidWizard:
    wizard = await _owned_wizard(wizard_id, current_user, db)
    if not isinstance(wizard.spec, list) or not wizard.spec:
        raise HTTPException(status_code=400, detail="请先生成编写大纲")
    if wizard.spec_stale:
        raise HTTPException(
            status_code=409,
            detail="大纲基于旧的需求/素材，请先重新生成或确认接受当前版本",
        )
    wizard.spec_confirmed_at = utc_now()
    await db.commit()
    await db.refresh(wizard)
    return wizard


# ------------------------------------------------------------------ 逐章撰写


async def _owned_writing_task(
    task_id: str, current_user, db: DBSession
) -> BidWritingTask:
    task = (
        await db.execute(select(BidWritingTask).where(BidWritingTask.id == task_id))
    ).scalar_one_or_none()
    if task is None or task.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="撰写任务不存在或无权访问")
    return task


async def _dispatch_writing_task(db: DBSession, task: BidWritingTask) -> BidWritingTask:
    from backend.services.task_lifecycle import add_task_dispatch, dispatch_task_outbox

    db.add(task)
    await db.flush()
    outbox = add_task_dispatch(db, task_kind="bid_wizard_write", task_id=task.id)
    await db.flush()
    task.celery_task_id = outbox.celery_task_id
    await db.commit()
    await db.refresh(task)
    await dispatch_task_outbox(outbox.id)
    return task


@router.post(
    "/wizards/{wizard_id}/writing-tasks",
    response_model=WritingTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_writing_task(
    wizard_id: str, body: WritingTaskCreate, db: DBSession, current_user: CurrentUser
) -> BidWritingTask:
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _require_access(db, current_user)
    if wizard.status != "active":
        raise HTTPException(status_code=400, detail="向导已结束，不能发起撰写")
    if not wizard.spec_confirmed_at:
        raise HTTPException(status_code=400, detail="请先确认编写大纲后再开始撰写")
    spec = wizard.spec if isinstance(wizard.spec, list) else []
    known = {node.get("node_id") for node in spec if isinstance(node, dict)}
    unknown = [node_id for node_id in body.node_ids if node_id not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"章节不存在于大纲中：{', '.join(unknown[:5])}")

    from backend.services.sales import multiplier_for_task
    from backend.services.task_lifecycle import authorize_billable_task_start

    sales_config = await authorize_billable_task_start(
        db, user_id=current_user.id, operation_name="AI 标书撰写"
    )
    task = BidWritingTask(
        wizard_id=wizard.id,
        project_id=wizard.project_id,
        user_id=current_user.id,
        selected_nodes=list(dict.fromkeys(body.node_ids)),
        status="pending",
        billing_multiplier=multiplier_for_task(sales_config, "bid_wizard_write"),
        billing_status="pending",
    )
    return await _dispatch_writing_task(db, task)


@router.get("/writing-tasks/latest", response_model=WritingTaskResponse)
async def get_latest_writing_task(
    db: DBSession,
    current_user: CurrentUser,
    wizard_id: str | None = Query(default=None, max_length=36),
) -> BidWritingTask:
    stmt = select(BidWritingTask).where(BidWritingTask.user_id == current_user.id)
    if wizard_id:
        stmt = stmt.where(BidWritingTask.wizard_id == wizard_id)
    row = (
        await db.execute(stmt.order_by(BidWritingTask.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="没有撰写任务")
    return row


@router.get("/writing-tasks/{task_id}", response_model=WritingTaskResponse)
async def get_writing_task(task_id: str, db: DBSession, current_user: CurrentUser) -> BidWritingTask:
    return await _owned_writing_task(task_id, current_user, db)


@router.get("/writing-tasks/{task_id}/sections", response_model=list[WizardSectionResponse])
async def list_writing_sections(
    task_id: str, db: DBSession, current_user: CurrentUser
) -> list[BidWizardSection]:
    task = await _owned_writing_task(task_id, current_user, db)
    rows = list(
        (
            await db.execute(
                select(BidWizardSection).where(BidWizardSection.task_id == task.id)
            )
        ).scalars().all()
    )
    return sorted(rows, key=lambda row: _node_sort_key(row.node_id))


@router.get(
    "/writing-tasks/{task_id}/sections/{node_id}", response_model=WizardSectionContentResponse
)
async def get_writing_section_content(
    task_id: str, node_id: str, db: DBSession, current_user: CurrentUser
) -> WizardSectionContentResponse:
    task = await _owned_writing_task(task_id, current_user, db)
    row = (
        await db.execute(
            select(BidWizardSection).where(
                BidWizardSection.task_id == task.id,
                BidWizardSection.node_id == node_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    content = None
    if row.content_path:
        try:
            content = _resolve_workspace_path(row.content_path).read_text(
                encoding="utf-8", errors="replace"
            )[:200_000]
        except Exception:
            content = None
    return WizardSectionContentResponse(
        node_id=row.node_id,
        title=row.title,
        status=row.status,
        content=content,
        word_count=row.word_count,
    )


@router.post(
    "/writing-tasks/{task_id}/sections/{node_id}/written",
    response_model=SectionWrittenResponse,
)
async def mark_section_written(
    task_id: str, node_id: str, db: DBSession, current_user: CurrentUser
) -> SectionWrittenResponse:
    """页面把该章写入 Word 成功后回报（ADR-0001 逐章写入链路的闭环点）。"""
    task = await _owned_writing_task(task_id, current_user, db)
    row = (
        await db.execute(
            select(BidWizardSection).where(
                BidWizardSection.task_id == task.id,
                BidWizardSection.node_id == node_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    if row.status == "failed":
        raise HTTPException(status_code=409, detail="章节生成失败，没有可写入的内容")
    if row.status not in ("generated", "written"):
        raise HTTPException(status_code=409, detail="章节尚未生成完成")
    row.status = "written"
    row.written_at = utc_now()
    await db.commit()
    return SectionWrittenResponse(node_id=row.node_id, status=row.status, written_at=row.written_at)


@router.post(
    "/writing-tasks/{task_id}/sections/{node_id}/regenerate",
    response_model=WritingTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def regenerate_writing_section(
    task_id: str, node_id: str, db: DBSession, current_user: CurrentUser
) -> BidWritingTask:
    """单章重生成：新建只含该章的任务（continue_of 指向旧任务），用当前 Spec。"""
    task = await _owned_writing_task(task_id, current_user, db)
    if task.status not in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="任务尚未结束，不能重生成章节")
    wizard = (
        await db.execute(select(BidWizard).where(BidWizard.id == task.wizard_id))
    ).scalar_one_or_none()
    if wizard is None or wizard.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="向导不存在或无权访问")
    spec = wizard.spec if isinstance(wizard.spec, list) else []
    if not any(isinstance(node, dict) and node.get("node_id") == node_id for node in spec):
        raise HTTPException(status_code=404, detail="章节不存在于当前大纲中")

    from backend.services.sales import multiplier_for_task
    from backend.services.task_lifecycle import authorize_billable_task_start

    sales_config = await authorize_billable_task_start(
        db, user_id=current_user.id, operation_name="AI 标书撰写"
    )
    new_task = BidWritingTask(
        wizard_id=wizard.id,
        project_id=task.project_id,
        user_id=current_user.id,
        selected_nodes=[node_id],
        continue_of=task.id,
        status="pending",
        billing_multiplier=multiplier_for_task(sales_config, "bid_wizard_write"),
        billing_status="pending",
    )
    return await _dispatch_writing_task(db, new_task)


@router.post("/writing-tasks/{task_id}/cancel", response_model=WritingTaskResponse)
async def cancel_writing_task(
    task_id: str, db: DBSession, current_user: CurrentUser
) -> BidWritingTask:
    task = await _owned_writing_task(task_id, current_user, db)
    if task.status in _TERMINAL_STATUSES:
        return task
    from backend.services.task_lifecycle import (
        cancel_pending_dispatch,
        enqueue_billing_settlement,
        finalize_task_usage,
    )

    cancelled_before_dispatch = await cancel_pending_dispatch(
        db, task_kind="bid_wizard_write", task_id=task_id
    )
    if task.celery_task_id and not cancelled_before_dispatch:
        try:
            from backend.celery_app import celery_app

            celery_app.control.revoke(task.celery_task_id, terminate=False)
        except Exception:
            pass
    try:
        from backend.tasks.bid_wizard_tasks import set_wizard_write_cancelled

        set_wizard_write_cancelled(task.id)
    except Exception:
        if task.celery_task_id and not cancelled_before_dispatch:
            from backend.celery_app import celery_app

            celery_app.control.revoke(task.celery_task_id, terminate=True)
    task.status = "cancelled"
    task.error_message = "用户取消了撰写任务"
    task.completed_at = utc_now()
    task.billing_status = "pending"
    if cancelled_before_dispatch:
        task.usage_finalized_at = utc_now()
    await db.commit()
    await db.refresh(task)
    if cancelled_before_dispatch:
        await finalize_task_usage("bid_wizard_write", task_id)
    else:
        enqueue_billing_settlement(
            "bid_wizard_write",
            task_id,
            countdown=get_settings().billing_orphan_finalize_grace_seconds,
        )
    return task


@router.get("/writing-tasks/{task_id}/stream")
async def stream_writing_task_events(
    task_id: str,
    request: Request,
    db: DBSession,
    current_user: CurrentUser,
):
    """SSE：逐章事件（section_started/completed/failed），前端据此逐章写入 Word。"""
    await _owned_writing_task(task_id, current_user, db)
    last_event_id = request.headers.get("Last-Event-ID")

    async def generator() -> AsyncGenerator[str, None]:
        async for event in sse_manager.connect(task_id, last_event_id):
            yield event

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
