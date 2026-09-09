"""AI编标（bid-wizard）APIs：四阶段向导（素材准备→需求确认→编写大纲→逐章撰写）.

与 V1 bid-draft 完全独立的模块（doc/workspace/20-bid-wizard.md §5.2）：
- 素材/招标文件 multipart 直传（插件与页面共用），material 上传即自动建索引任务（决策 17a）；
- 问卷生成 / Spec 生成与修订 = 同步微任务（bid_wizard_qa，请求内执行、结束即结算）；
- 撰写任务 = outbox → generation 队列（bid_wizard_write）；written 由页面写入 Word 后回报。
"""

from __future__ import annotations

import asyncio
import logging
import re
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
    BidWizardSetting,
    BidWizardWhitelist,
    BidWritingTask,
    Document,
    Project,
)
from backend.schemas.bid_wizard import (
    QuestionnaireAnswer,
    RequirementsUpdate,
    SectionWrittenResponse,
    SectionsResetWrittenResponse,
    SpecRevise,
    SpecUpdate,
    WizardAccessResponse,
    WizardCreate,
    WizardEstimateResponse,
    WizardFollowupAnswer,
    WizardFollowupsResponse,
    WizardListItem,
    WizardListResponse,
    WizardMaterialIndexResponse,
    WizardMaterialResponse,
    WizardQaAdopt,
    WizardQaAsk,
    WizardQaAskResponse,
    WizardResponse,
    WizardSectionContentResponse,
    WizardSectionLatestResponse,
    WizardSectionResponse,
    WizardStageUpdate,
    WritingTaskBrief,
    WritingTaskCreate,
    WritingTaskResponse,
)
from backend.services.sse_service import sse_manager
from backend.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bid-wizard", tags=["Bid Wizard"])

_STAGE_ORDER = {"material": 0, "requirement": 1, "outline": 2, "writing": 3}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_QA_TIMEOUT_SECONDS = 120  # 轻交互（AI 修订 / 追问 / 反问）
# 重交互（解读 / 问卷 / Spec 生成）：2026-09-09 联调实测，9 份素材索引 + 11k 解读
# 的问卷生成在 TokenHub 上 >120s，被一刀切超时打成「AI 处理超时」——按动作分级放宽。
_QA_HEAVY_TIMEOUT_SECONDS = 300
# 默认项目名「AI编标 YYYY-MM-DD」（决策 26：仍是默认名时，传招标文件后自动改用文件名）
_DEFAULT_PROJECT_NAME_RE = re.compile(r"^AI编标 \d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------------ helpers


async def _effective_access_mode(db: DBSession) -> str:
    """开关模式（决策 36）：bid_wizard_settings DB 行优先，env 降为安装初始值/回退。

    env 是进程级缓存（lru_cache get_settings），改了要重启；DB 行每次请求直读，
    运营台经 internal API 推送后即时生效。"""
    row = (
        await db.execute(select(BidWizardSetting).where(BidWizardSetting.id == "default"))
    ).scalar_one_or_none()
    if row is not None and row.mode in ("enabled", "whitelist", "disabled"):
        return row.mode
    mode = (get_settings().bid_wizard_access_mode or "disabled").strip().lower()
    return mode if mode in ("enabled", "whitelist", "disabled") else "disabled"


async def _require_access(db: DBSession, current_user) -> None:
    """Feature gate: enabled / whitelist / disabled（DB 优先，见 _effective_access_mode）."""
    mode = await _effective_access_mode(db)
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
    # join 过滤软删项目（决策 29：删除后向导的一切端点 404，审计数据仍在库）
    wizard = (
        await db.execute(
            select(BidWizard)
            .join(Project, Project.id == BidWizard.project_id)
            .where(BidWizard.id == wizard_id, Project.is_deleted.is_(False))
        )
    ).scalar_one_or_none()
    if wizard is None or wizard.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="向导不存在或无权访问")
    return wizard


async def _guard_writing_idle(db: DBSession, wizard: BidWizard) -> None:
    """决策 14：撰写中 spec 锁定——有进行中的撰写任务时禁止改 Spec/需求/阶段。"""
    row = (
        await db.execute(
            select(BidWritingTask.id).where(
                BidWritingTask.wizard_id == wizard.id,
                BidWritingTask.status.in_(("pending", "running")),
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        raise HTTPException(
            status_code=409,
            detail="撰写任务进行中，Spec 已锁定；请等任务结束或取消后再修改",
        )


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


async def _wizard_tender_documents(db: DBSession, wizard: BidWizard) -> list[Document]:
    """向导的全部招标文件（2026-09-09 联调改多文件口径：正文 + 补遗/澄清等可传多份）."""
    rows = (
        await db.execute(
            select(Document)
            .where(
                Document.project_id == wizard.project_id,
                Document.doc_type == "tender",
            )
            .order_by(Document.created_at.asc())
        )
    ).scalars().all()
    return list(rows)


async def _require_parsed_tenders(db: DBSession, wizard: BidWizard) -> list[Document]:
    """解读/问卷消费前的齐备校验：至少一份、无解析中、无解析失败."""
    documents = await _wizard_tender_documents(db, wizard)
    if not documents:
        raise HTTPException(status_code=400, detail="请先上传招标文件")
    parsing = [item for item in documents if item.status in ("pending", "parsing")]
    if parsing:
        raise HTTPException(status_code=409, detail="招标文件尚未解析完成，请稍候")
    failed = next((item for item in documents if item.status != "parsed"), None)
    if failed is not None:
        raise HTTPException(
            status_code=400,
            detail=f"招标文件《{failed.original_filename}》解析失败，请删除后重新上传",
        )
    return documents


def _merged_tender_markdown(documents: list[Document]) -> str:
    """合并多份招标文件的解析产物（多份时按文件名分节，交由 analyze_tender 统一截断）."""
    parts: list[str] = []
    for document in documents:
        markdown = _resolve_workspace_path(document.parsed_markdown_path or "").read_text(
            encoding="utf-8", errors="replace"
        )
        if len(documents) > 1:
            parts.append(f"# 来源文件：{document.original_filename}\n\n{markdown}")
        else:
            parts.append(markdown)
    return "\n\n".join(parts)


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
    timeout_seconds: int = _QA_TIMEOUT_SECONDS,
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
    # 任务行去重（§5.4 失败重试幂等）：同向导同类 QA 任务进行中时拒绝重复发起，
    # 防双击/网络重试造成并行双计费。
    running = (
        await db.execute(
            select(BidWizardQaTask.id).where(
                BidWizardQaTask.wizard_id == wizard.id,
                BidWizardQaTask.action == action,
                BidWizardQaTask.status == "running",
            )
        )
    ).scalar_one_or_none()
    if running is not None:
        raise HTTPException(status_code=409, detail="同类任务正在处理中，请稍候再试")
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
            result = await asyncio.wait_for(runner(), timeout=timeout_seconds)
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
    mode = await _effective_access_mode(db)
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
async def estimate_index_cost(
    db: DBSession, bytes_size: int = Query(..., ge=1, le=2_147_483_647)
) -> WizardEstimateResponse:
    """上传前预估：字数→token 量级→约 X 点（决策 17a 的确认弹窗用）。

    office/pdf 是压缩容器，按 ~0.4 的文本占比粗估；点数按 1 次索引调用的
    假设口径线性换算（见 agent.estimate_index_points），实际计费按真实用量结算；
    价目缺失时 estimated_points 为 null，前端退回 token 量级提示。
    """
    chars = max(1_000, int(bytes_size * 0.4))
    from backend.agent.bid_wizard_agent import estimate_index_points, estimate_tokens
    from backend.services.sales import get_sales_config, multiplier_for_task

    tokens = estimate_tokens(chars)
    sales_config = await get_sales_config(db)
    multiplier = multiplier_for_task(sales_config, "bid_wizard_index")
    return WizardEstimateResponse(
        chars=chars,
        estimated_tokens=tokens,
        estimated_points=estimate_index_points(tokens, multiplier),
    )


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
    """入口恢复：当前用户最近一个活动向导（跨项目，断点续作；软删项目不返回）。"""
    wizard = (
        await db.execute(
            select(BidWizard)
            .join(Project, Project.id == BidWizard.project_id)
            .where(
                BidWizard.user_id == current_user.id,
                BidWizard.status == "active",
                Project.is_deleted.is_(False),
            )
            .order_by(BidWizard.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if wizard is None:
        raise HTTPException(status_code=404, detail="当前没有进行中的 AI编标 向导")
    return wizard


@router.get("/wizards", response_model=WizardListResponse)
async def list_wizards(db: DBSession, current_user: CurrentUser) -> WizardListResponse:
    """项目列表页聚合（§4.0）：进行中 + 已归档两组，updated_at 倒序，软删项目不显示。"""
    await _require_access(db, current_user)
    rows = (
        await db.execute(
            select(BidWizard, Project)
            .join(Project, Project.id == BidWizard.project_id)
            .where(BidWizard.user_id == current_user.id, Project.is_deleted.is_(False))
            .order_by(BidWizard.updated_at.desc())
        )
    ).all()

    project_ids = {project.id for _, project in rows}
    tender_by_project: dict[str, str] = {}
    if project_ids:
        tender_rows = (
            await db.execute(
                select(Document.project_id, Document.original_filename)
                .where(Document.project_id.in_(project_ids), Document.doc_type == "tender")
                .order_by(Document.created_at.desc())
            )
        ).all()
        for project_id, filename in tender_rows:  # 最新一行先到先得
            if filename:
                tender_by_project.setdefault(project_id, filename)

    wizard_ids = {wizard.id for wizard, _ in rows}
    latest_task_by_wizard: dict[str, BidWritingTask] = {}
    if wizard_ids:
        task_rows = (
            await db.execute(
                select(BidWritingTask)
                .where(BidWritingTask.wizard_id.in_(wizard_ids))
                .order_by(BidWritingTask.created_at.desc())
            )
        ).scalars().all()
        for task in task_rows:
            latest_task_by_wizard.setdefault(task.wizard_id, task)

    def _item(wizard: BidWizard, project: Project) -> WizardListItem:
        latest = latest_task_by_wizard.get(wizard.id)
        return WizardListItem(
            wizard_id=wizard.id,
            project_id=project.id,
            project_name=project.name,
            stage=wizard.stage,
            status=wizard.status,
            tender_filename=tender_by_project.get(project.id),
            latest_writing_task=(
                WritingTaskBrief(id=latest.id, status=latest.status) if latest else None
            ),
            updated_at=wizard.updated_at,
            created_at=wizard.created_at,
        )

    return WizardListResponse(
        active=[_item(w, p) for w, p in rows if w.status == "active"],
        archived=[_item(w, p) for w, p in rows if w.status == "archived"],
    )


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
    await _guard_writing_idle(db, wizard)
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


@router.post("/wizards/{wizard_id}/archive", response_model=WizardResponse)
async def archive_wizard(wizard_id: str, db: DBSession, current_user: CurrentUser) -> BidWizard:
    """归档向导：数据保留在云端，但不再被入口 ``/wizards/active`` 恢复（「新建项目」的支撑端点）。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    if wizard.status != "active":
        raise HTTPException(status_code=400, detail="向导已结束，无需归档")
    running = (
        await db.execute(
            select(BidWritingTask.id).where(
                BidWritingTask.wizard_id == wizard.id,
                BidWritingTask.status.in_(("pending", "running")),
            )
        )
    ).scalar_one_or_none()
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail="撰写任务进行中，请等待完成或取消后再新建项目",
        )
    wizard.status = "archived"
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.post("/wizards/{wizard_id}/restore", response_model=WizardResponse)
async def restore_wizard(wizard_id: str, db: DBSession, current_user: CurrentUser) -> BidWizard:
    """恢复归档（决策 28）：status 回 active、回归档前 stage 断点；可逆动作，无守卫冲突——
    归档时已挡撰写中，per-project 唯一索引也不冲突（归档向导本就是该项目唯一向导）。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    if wizard.status != "archived":
        raise HTTPException(status_code=400, detail="向导未归档，无需恢复")
    wizard.status = "active"
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.delete("/wizards/{wizard_id}", response_model=WizardResponse)
async def delete_wizard(wizard_id: str, db: DBSession, current_user: CurrentUser) -> BidWizard:
    """删除项目（决策 29）：仅已归档可删（先归档再删，两级缓冲）；软删 project（审计保留，
    向导/素材/任务行原样但经联查不可见）+ 异步物理清理 workspace；计费流水不动。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    if wizard.status != "archived":
        raise HTTPException(status_code=400, detail="请先归档项目后再删除")
    running = (
        await db.execute(
            select(BidWritingTask.id).where(
                BidWritingTask.wizard_id == wizard.id,
                BidWritingTask.status.in_(("pending", "running")),
            )
        )
    ).scalar_one_or_none()
    if running is not None:
        raise HTTPException(status_code=409, detail="撰写任务进行中，请等待完成或取消后再删除")
    project = (
        await db.execute(select(Project).where(Project.id == wizard.project_id))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    project.is_deleted = True
    project.deleted_at = utc_now()
    project.deleted_by_user_id = current_user.id
    await db.commit()
    from backend.tasks.bid_wizard_tasks import cleanup_wizard_workspace

    cleanup_wizard_workspace.delay(wizard.id, current_user.id, project.id)
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
    """上传一份招标文件；支持多份（正文 + 补遗/澄清等，格式同标书检查：pdf/docx/doc/xlsx）.

    任意招标文件变化（新增/删除）都清空旧解读（wizard.analysis）：解读基于
    全部招标文件的合并内容，而问卷生成会静默复用 analysis，不清会导致文件集
    变化后问卷仍基于旧招标要素生成。
    """
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _require_access(db, current_user)
    wizard.analysis = None
    document = await _store_wizard_document(db, wizard, current_user, file, doc_type="tender")
    # 决策 26：项目名仍是默认名（AI编标 YYYY-MM-DD）时，自动改用招标文件名（去扩展名）
    project = (
        await db.execute(select(Project).where(Project.id == wizard.project_id))
    ).scalar_one_or_none()
    if project is not None and _DEFAULT_PROJECT_NAME_RE.match(project.name or ""):
        stem = Path(file.filename or "").stem.strip()
        if stem:
            project.name = stem[:200]
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
async def delete_all_tenders(wizard_id: str, db: DBSession, current_user: CurrentUser) -> BidWizard:
    """删除全部招标文件（清空口径）。逐份删除请用 DELETE /tender/{document_id}."""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    for document in await _wizard_tender_documents(db, wizard):
        await db.delete(document)
    wizard.analysis = None
    _mark_downstream_stale(wizard)
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.delete("/wizards/{wizard_id}/tender/{document_id}", response_model=WizardResponse)
async def delete_tender_document(
    wizard_id: str, document_id: str, db: DBSession, current_user: CurrentUser
) -> BidWizard:
    """删除一份招标文件；剩余文件集变化同样清空旧解读."""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    document = (
        await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.project_id == wizard.project_id,
                Document.doc_type == "tender",
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="招标文件不存在")
    await db.delete(document)
    wizard.analysis = None
    _mark_downstream_stale(wizard)
    await db.commit()
    await db.refresh(wizard)
    return wizard


@router.post("/wizards/{wizard_id}/analysis", response_model=WizardResponse)
async def analyze_wizard_tender(
    wizard_id: str, db: DBSession, current_user: CurrentUser
) -> BidWizard:
    """同步微任务：AI 解读招标文件 → 招标要素 + suggested_materials（§4.2 阶段 1 建议补素材）。

    多份招标文件合并解读（正文+补遗/澄清）；问卷生成会复用已存的 analysis
    （不重复计费）；重新解读会覆盖旧结果。
    """
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _require_access(db, current_user)
    documents = await _require_parsed_tenders(db, wizard)
    tender_markdown = _merged_tender_markdown(documents)

    async def runner() -> dict[str, Any]:
        from backend.agent.bid_wizard_agent import WizardLLM, analyze_tender

        llm = WizardLLM(timeout=_QA_HEAVY_TIMEOUT_SECONDS)
        return await analyze_tender(llm, tender_markdown)

    wizard.analysis = await _run_qa_task(
        db,
        wizard,
        current_user,
        action="tender_analysis",
        runner=runner,
        timeout_seconds=_QA_HEAVY_TIMEOUT_SECONDS,
    )
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
    documents = await _require_parsed_tenders(db, wizard)

    wizard_id_ref = wizard.id
    analysis_ref = wizard.analysis if isinstance(wizard.analysis, dict) and wizard.analysis else None
    tender_markdown = None if analysis_ref is not None else _merged_tender_markdown(documents)

    async def runner() -> dict[str, Any]:
        from backend.agent.bid_wizard_agent import (
            WizardLLM,
            analyze_tender,
            build_material_index_text,
            generate_questionnaire as gen_q,
            _load_material_index_rows,
        )

        llm = WizardLLM(timeout=_QA_HEAVY_TIMEOUT_SECONDS)
        analysis = analysis_ref
        if analysis is None:
            analysis = await analyze_tender(llm, tender_markdown or "")
        material_entries = await _load_material_index_rows(db, wizard_id_ref)
        return {
            "analysis": analysis,
            "questionnaire": await gen_q(
                llm,
                analysis=analysis,
                material_index_text=build_material_index_text(material_entries),
            ),
        }

    payload = await _run_qa_task(
        db,
        wizard,
        current_user,
        action="questionnaire",
        runner=runner,
        timeout_seconds=_QA_HEAVY_TIMEOUT_SECONDS,
    )
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
    await _guard_writing_idle(db, wizard)
    from backend.agent.bid_wizard_agent import merge_questionnaire_answers

    merged = merge_questionnaire_answers(
        wizard.questionnaire, [answer.model_dump() for answer in body.answers]
    )
    # 追问侧栏采纳对与反问补答（supplementals）跨保存保留：重答问卷不丢补充说明
    previous = wizard.requirements if isinstance(wizard.requirements, dict) else {}
    for key in ("supplementals", "followups"):
        if isinstance(previous.get(key), list):
            merged[key] = previous[key]
    wizard.requirements = merged
    wizard.requirements_stale = False
    # 答案变了 → 基于旧答案的 Spec 过期（决策 19）
    if wizard.spec:
        wizard.spec_stale = True
    await db.commit()
    await db.refresh(wizard)
    return wizard


# ------------------------------------------------------------------ 多轮追问（决策 32）


@router.post("/wizards/{wizard_id}/qa/ask", response_model=WizardQaAskResponse)
async def ask_sidebar_question(
    wizard_id: str, body: WizardQaAsk, db: DBSession, current_user: CurrentUser
) -> WizardQaAskResponse:
    """追问侧栏（决策 32a）：自由提问，AI 基于招标要素+已确认需求+素材索引作答；每轮一次 qa 计费。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _require_access(db, current_user)
    if not await _wizard_tender_documents(db, wizard):
        raise HTTPException(status_code=400, detail="请先上传招标文件")
    question = body.question.strip()
    wizard_ref = wizard

    async def runner() -> str:
        from backend.agent.bid_wizard_agent import (
            WizardLLM,
            _load_material_index_rows,
            answer_sidebar_question,
            build_material_index_text,
            build_requirements_text,
        )

        llm = WizardLLM(timeout=_QA_TIMEOUT_SECONDS)
        material_entries = await _load_material_index_rows(db, wizard_ref.id)
        return await answer_sidebar_question(
            llm,
            question=question,
            analysis=wizard_ref.analysis if isinstance(wizard_ref.analysis, dict) else {},
            requirements_text=build_requirements_text(wizard_ref.requirements),
            material_index_text=build_material_index_text(material_entries),
        )

    answer = await _run_qa_task(db, wizard, current_user, action="qa_ask", runner=runner)
    return WizardQaAskResponse(answer=str(answer))


@router.post("/wizards/{wizard_id}/qa/adopt", response_model=WizardResponse)
async def adopt_sidebar_answer(
    wizard_id: str, body: WizardQaAdopt, db: DBSession, current_user: CurrentUser
) -> BidWizard:
    """采纳追问问答对并入编写需求 supplementals（决策 32a）；无 LLM 不计费。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    if not isinstance(wizard.requirements, dict) or not wizard.requirements:
        raise HTTPException(status_code=400, detail="请先保存问卷答案，再采纳追问补充说明")
    await _guard_writing_idle(db, wizard)
    requirements = dict(wizard.requirements)
    supplementals = [
        item for item in requirements.get("supplementals") or [] if isinstance(item, dict)
    ]
    question = body.question.strip()[:2_000]
    answer = body.answer.strip()[:4_000]
    if not any(
        str(item.get("question")) == question and str(item.get("answer")) == answer
        for item in supplementals
    ):
        supplementals.append(
            {"question": question, "answer": answer, "adopted_at": utc_now().isoformat()}
        )
        requirements["supplementals"] = supplementals
        wizard.requirements = requirements
        if wizard.spec:
            wizard.spec_stale = True
        await db.commit()
    await db.refresh(wizard)
    return wizard


@router.post("/wizards/{wizard_id}/requirements/followups", response_model=WizardFollowupsResponse)
async def generate_followups(
    wizard_id: str, db: DBSession, current_user: CurrentUser
) -> WizardFollowupsResponse:
    """AI 主动反问（决策 32b/Q19）：检测已保存需求的缺口 → ≤3 条追问卡片。
    单轮：已有未处理追问时不重复检测（前端只在保存后自动触发一次）。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _require_access(db, current_user)
    if not isinstance(wizard.requirements, dict) or not wizard.requirements:
        raise HTTPException(status_code=400, detail="请先保存问卷答案")
    await _guard_writing_idle(db, wizard)
    existing = wizard.requirements.get("followups")
    if isinstance(existing, list) and existing:
        return WizardFollowupsResponse(followups=existing)
    wizard_ref = wizard

    async def runner() -> list[dict[str, Any]]:
        from backend.agent.bid_wizard_agent import (
            WizardLLM,
            _load_material_index_rows,
            build_material_index_text,
            build_requirements_text,
            generate_followup_questions,
        )

        llm = WizardLLM(timeout=_QA_TIMEOUT_SECONDS)
        material_entries = await _load_material_index_rows(db, wizard_ref.id)
        return await generate_followup_questions(
            llm,
            analysis=wizard_ref.analysis if isinstance(wizard_ref.analysis, dict) else {},
            requirements_text=build_requirements_text(wizard_ref.requirements),
            material_index_text=build_material_index_text(material_entries),
        )

    followups = await _run_qa_task(db, wizard, current_user, action="followups", runner=runner)
    requirements = dict(wizard.requirements)
    requirements["followups"] = followups
    wizard.requirements = requirements
    await db.commit()
    await db.refresh(wizard)
    return WizardFollowupsResponse(followups=followups)


@router.post(
    "/wizards/{wizard_id}/requirements/followup-answer", response_model=WizardResponse
)
async def answer_followup(
    wizard_id: str, body: WizardFollowupAnswer, db: DBSession, current_user: CurrentUser
) -> BidWizard:
    """反问作答/跳过（Q19：非阻塞、不计费）：answered 并入 supplementals，卡片标记已处理。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    if not isinstance(wizard.requirements, dict):
        raise HTTPException(status_code=400, detail="请先保存问卷答案")
    await _guard_writing_idle(db, wizard)
    followups = [
        item for item in wizard.requirements.get("followups") or [] if isinstance(item, dict)
    ]
    target = next((item for item in followups if str(item.get("id")) == body.followup_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="追问不存在或已被处理")
    if target.get("status"):
        await db.refresh(wizard)
        return wizard  # 幂等：已处理过的重复提交直接返回
    requirements = dict(wizard.requirements)
    if body.action == "answered":
        answer = (body.answer or "").strip()[:4_000]
        if not answer:
            raise HTTPException(status_code=400, detail="回答内容不能为空，可选择跳过")
        supplementals = [
            item for item in requirements.get("supplementals") or [] if isinstance(item, dict)
        ]
        supplementals.append(
            {
                "question": str(target.get("question") or "")[:2_000],
                "answer": answer,
                "source": "followup",
            }
        )
        requirements["supplementals"] = supplementals
        target["status"] = "answered"
    else:
        target["status"] = "skipped"
    requirements["followups"] = followups
    wizard.requirements = requirements
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
    await _guard_writing_idle(db, wizard)

    wizard_ref = wizard

    async def runner() -> list[dict[str, Any]]:
        from backend.agent.bid_wizard_agent import (
            WizardLLM,
            _load_material_index_rows,
            build_material_index_text,
            build_requirements_text,
            generate_spec as gen_spec,
        )

        llm = WizardLLM(timeout=_QA_HEAVY_TIMEOUT_SECONDS)
        material_entries = await _load_material_index_rows(db, wizard_ref.id)
        return await gen_spec(
            llm,
            analysis=wizard_ref.analysis if isinstance(wizard_ref.analysis, dict) else {},
            requirements_text=build_requirements_text(wizard_ref.requirements),
            material_index_text=build_material_index_text(material_entries),
        )

    spec = await _run_qa_task(
        db,
        wizard,
        current_user,
        action="spec_generate",
        runner=runner,
        timeout_seconds=_QA_HEAVY_TIMEOUT_SECONDS,
    )
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
    await _guard_writing_idle(db, wizard)
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
    await _guard_writing_idle(db, wizard)

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
    await _guard_writing_idle(db, wizard)
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


@router.post(
    "/wizards/{wizard_id}/sections/reset-written",
    response_model=SectionsResetWrittenResponse,
)
async def reset_written_sections(
    wizard_id: str, db: DBSession, current_user: CurrentUser
) -> SectionsResetWrittenResponse:
    """移除全部 AI 内容后的服务端状态回退（决策 31）：该向导全部任务中 written 章节
    回到 generated（written_at 清空），供「重新写入 Word」批量动作消费。
    守卫：撰写任务进行中不允许回退（移除按钮此时本就置灰，双保险）。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    await _guard_writing_idle(db, wizard)
    task_rows = (
        await db.execute(select(BidWritingTask.id).where(BidWritingTask.wizard_id == wizard.id))
    ).scalars().all()
    reset_count = 0
    if task_rows:
        sections = (
            await db.execute(
                select(BidWizardSection).where(
                    BidWizardSection.task_id.in_(task_rows),
                    BidWizardSection.status == "written",
                )
            )
        ).scalars().all()
        for row in sections:
            row.status = "generated"
            row.written_at = None
            reset_count += 1
        await db.commit()
    return SectionsResetWrittenResponse(reset_count=reset_count)


@router.get(
    "/wizards/{wizard_id}/sections/latest",
    response_model=list[WizardSectionLatestResponse],
)
async def list_latest_sections(
    wizard_id: str, db: DBSession, current_user: CurrentUser
) -> list[WizardSectionLatestResponse]:
    """跨任务取每个 node 的最新章节行（「重新写入 Word」的权威清单）：
    同一 node 出现在多个任务（单章重生成）时取最新任务的那行，并按大纲序返回。"""
    wizard = await _owned_wizard(wizard_id, current_user, db)
    rows = (
        await db.execute(
            select(BidWizardSection, BidWritingTask)
            .join(BidWritingTask, BidWritingTask.id == BidWizardSection.task_id)
            .where(BidWritingTask.wizard_id == wizard.id)
            .order_by(BidWritingTask.created_at.desc())
        )
    ).all()
    latest_by_node: dict[str, tuple[BidWritingTask, BidWizardSection]] = {}
    for section, task_row in rows:  # 最新任务先到先得
        latest_by_node.setdefault(section.node_id, (task_row, section))
    ordered = sorted(
        latest_by_node.values(), key=lambda pair: _node_sort_key(pair[1].node_id)
    )
    return [
        WizardSectionLatestResponse(
            task_id=task_row.id,
            node_id=section.node_id,
            title=section.title,
            status=section.status,
            word_count=section.word_count,
        )
        for task_row, section in ordered
    ]


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
