"""图片预识别服务（doc 25 号方案一）：批量 OCR + 类型标签 + 跨任务缓存。

审查任务启动前对项目全部已解析文档的落盘图片批量 OCR：

- 结果持久化 ``document_image_annotations``（同一张图全库只识别一次，
  failed 视为终态不重试）；
- 标签（关键词词表命中）与首行摘要写入任务内 store，由
  get_section_images / doc_search 输出时附在图片名后——子 agent 从
  「按文件名盲选」变为「按内容类型选图」，根治「证明材料藏在未识别
  图片中」被断言「未提供」的误报（2026-11-11 doc 25 号事故）。

预识别复用 BaiduOcrTool 执行通道（归一化/重试/用量记录全继承），并显式
设置 ``seal_required=False`` 的 UsageContext——印章检测仅 E001 需要，
预识别逐张发印章请求纯属浪费且拖慢一倍。
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import threading
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.models import Document, DocumentImageAnnotation, User
from backend.services.usage_context import (
    UsageContext,
    reset_usage_context,
    set_usage_context,
)

logger = logging.getLogger(__name__)

# 参与预识别的位图后缀；wmf/emf 等矢量格式 OCR 通道不支持，跳过
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}

# OCR 文本中的印章小节起始标记（BaiduOcrTool 返回尾部可能附带）
_SEAL_SECTION_MARK = "【印章检测】"


# ---- 标签词表（有序扫描，首条命中即停；mode: all=全部命中, any=任一命中）----
# 设计原则：限定词组合的规则在前（如「事务所+执照」），宽泛规则在后（如单「营业执照」），
# 避免投标人的营业执照抢走会计师事务所营业执照的标签；「会计师事务所」
# 单词兜底放最后，避免抢走审计报告首页（事务所名+审计报告同页）的标签。
_LABEL_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("会计师事务所营业执照", "all", ("会计师事务所", "营业执照")),
    ("会计师事务所执业证书", "all", ("会计师事务所", "执业证书")),
    # 截图特异词须在「审计报告」前：查验截图页常含「审计报告查验结果」字样，
    # 先命中审计报告会让赋码查验凭证（B001 赋码核验依据）失去独立标签。
    ("查询/公示截图", "any", ("统一监管平台", "信用中国", "官网截图", "查验结果")),
    ("审计报告", "any", ("审计报告", "审字[", "审字（", "审计意见")),
    ("财务报表", "any", ("资产负债表", "利润表", "现金流量表", "所有者权益变动表")),
    ("财务状况说明书", "any", ("财务状况说明书",)),
    ("注册会计师证书", "any", ("注册会计师证书", "注册会计师.", "CPA")),
    ("纳税证明", "any", ("完税证明", "纳税证明", "税收缴款书")),
    ("社保证明", "any", ("社会保险费", "社保缴纳", "缴纳社会保险", "社会保险费缴纳")),
    ("银行凭证", "any", ("回单", "进账单", "转账凭证", "业务回执", "电子回单")),
    ("开户许可证", "any", ("开户许可证", "基本存款账户")),
    ("营业执照", "any", ("营业执照",)),
    ("资质证书", "any", ("资质证书", "资质等级", "建筑业企业资质", "资质等级证书")),
    ("安全生产许可证", "any", ("安全生产许可证",)),
    ("竣工验收材料", "any", ("竣工验收", "验收记录", "验收申请", "竣工验收备案")),
    ("中标通知书", "any", ("中标通知书", "中标结果", "成交通知书")),
    ("业绩合同", "any", ("合同编号", "合同书", "协议书", "施工合同")),
    ("人员证书", "any", ("建造师", "安全生产考核合格证书", "执业资格证书", "职称证书", "岗位证书")),
    ("身份证明", "any", ("居民身份证", "法定代表人身份证明", "身份证明书")),
    ("查询/公示截图", "any", ("查询结果", "公示")),
    ("财务附注", "any", ("附注", "会计报表附注")),
    ("会计师事务所文件", "any", ("会计师事务所", "会计师协会")),
]

# 摘要最长字符数（首行文本，作无标签时的类型线索）
_SUMMARY_MAX_CHARS = 40
# 摘要候选行跳过的「无信息量」行：纯数字/极短行
_MIN_SUMMARY_LINE_LEN = 6


def classify_ocr_text(ocr_text: str | None) -> tuple[str | None, str | None]:
    """对 OCR 全文打类型标签，返回 (label, summary)。

    label 未命中词表时为 None；summary 取首个有信息量的行（截断到 40 字）。
    """
    if not ocr_text:
        return None, None
    # 剥离印章小节（预识别上下文已关印章，防御性处理）
    text = ocr_text.split(_SEAL_SECTION_MARK, 1)[0]

    label = None
    for rule_label, mode, keywords in _LABEL_RULES:
        if mode == "all":
            hit = all(kw in text for kw in keywords)
        else:
            hit = any(kw in text for kw in keywords)
        if hit:
            label = rule_label
            break

    summary = None
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) >= _MIN_SUMMARY_LINE_LEN and not stripped.replace(".", "").replace(",", "").isdigit():
            summary = stripped[:_SUMMARY_MAX_CHARS]
            break
    return label, summary


def format_annotation_display(label: str | None, summary: str | None) -> str:
    """渲染注入工具输出的后缀串；无任何信息返回空串。"""
    if label and summary:
        return f"{label}（{summary}）"
    return label or summary or ""


# ---- 任务内标注 store（doc_name -> {image_name: display}）----
# 与审查任务同生命周期：预识别完成后写入、任务 finally 清空。celery worker
# 内同一任务为串行执行，无跨任务并发写；锁仅防御性兜底。
_DOC_ANNOTATIONS: dict[str, dict[str, str]] = {}
_STORE_LOCK = threading.Lock()


def set_doc_annotations(doc_name: str, mapping: dict[str, str]) -> None:
    """写入某文档的 {图片文件名: 展示后缀} 全量映射。"""
    with _STORE_LOCK:
        _DOC_ANNOTATIONS[doc_name] = dict(mapping)


def get_annotation_display(doc_name: str | None, image_name: str) -> str:
    """取某文档某图片的展示后缀；无标注返回空串。"""
    if not doc_name:
        return ""
    with _STORE_LOCK:
        return _DOC_ANNOTATIONS.get(doc_name, {}).get(image_name, "")


def clear_annotation_store() -> None:
    """清空任务内标注 store（任务结束时调用）。"""
    with _STORE_LOCK:
        _DOC_ANNOTATIONS.clear()


async def _build_usage_context(
    session: AsyncSession, user_id: str, task_id: str, project_id: str
) -> UsageContext:
    """构造预识别用 UsageContext（seal_required 恒 False）。

    反查本地 User 拿 sys_user 维度的口径与 SubAgentExecutor 一致；
    UsageContext 同时让 BaiduOcrTool 的用量记录正确归属到本任务。
    """
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    return UsageContext(
        external_user_id=user.external_user_id if user else None,
        local_user_id=user_id,
        user_name=(user.username if user else user_id) or str(user_id),
        enterprise_name=user.enterprise_name if user else None,
        interior_user=bool(user.interior_user) if user and user.interior_user is not None else False,
        project_id=project_id,
        task_id=task_id,
        todo_id=None,
        seal_required=False,
    )


async def _annotate_document(
    session: AsyncSession,
    document: Document,
    ocr_tool,
    semaphore: asyncio.Semaphore,
    max_images: int,
) -> tuple[int, int]:
    """识别单文档的未标注图片并入库，返回 (新增数, 失败数)；结果同时写 store。"""
    images_dir = Path(document.parsed_images_dir)
    if not images_dir.is_dir():
        logger.warning("[preannotate] images dir 不存在: %s", images_dir)
        return 0, 0

    existing = (
        await session.execute(
            select(DocumentImageAnnotation.image_name).where(
                DocumentImageAnnotation.document_id == document.id
            )
        )
    ).scalars().all()
    existing_names = set(existing)

    pending = [
        p for p in sorted(images_dir.iterdir())
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES and p.name not in existing_names
    ]
    if len(pending) > max_images:
        logger.info(
            "[preannotate] %s 待识别 %d 张超过单任务上限 %d，本次截断（其余下次任务继续）",
            document.original_filename, len(pending), max_images,
        )
        pending = pending[:max_images]

    async def recognize_one(path: Path) -> tuple[Path, str | None]:
        async with semaphore:
            result = await ocr_tool.execute(
                prompt="文档图片预识别（内容类型标注）",
                image_source=str(path),
            )
        return path, (result.content if result.success else None)

    results = await asyncio.gather(*(recognize_one(p) for p in pending))

    failed = 0
    rows: list[DocumentImageAnnotation] = []
    for path, text in results:
        label, summary = classify_ocr_text(text)
        status = "ok" if text is not None else "failed"
        if status == "failed":
            failed += 1
        rows.append(DocumentImageAnnotation(
            id=str(uuid.uuid4()),
            document_id=document.id,
            image_name=path.name,
            label=label,
            summary=summary,
            ocr_text=text,
            provider="baidu",
            status=status,
        ))
    if rows:
        session.add_all(rows)

    # store 写全量映射（含历史标注），供工具输出时查询。
    # 本批 add_all 的行随 select 的 autoflush 一并可见；dict 按文件名去重，
    # 仅 status=ok 进映射（failed 视为未识别，由「无法确认」门禁兜底）。
    all_rows: list[DocumentImageAnnotation] = list(rows)
    all_rows += (
        await session.execute(
            select(DocumentImageAnnotation).where(
                DocumentImageAnnotation.document_id == document.id,
                DocumentImageAnnotation.status == "ok",
            )
        )
    ).scalars().all()
    mapping = {
        row.image_name: format_annotation_display(row.label, row.summary)
        for row in all_rows
        if row.status == "ok"
    }
    set_doc_annotations(document.original_filename, mapping)
    return len(rows), failed


async def preannotate_project_documents(
    session: AsyncSession,
    project_id: str,
    user_id: str,
    task_id: str,
) -> dict[str, int]:
    """对项目全部已解析文档批量预识别，返回统计 {"docs": n, "new": m, "failed": k}。

    - 配置开关关闭时直接返回（store 为空，工具输出维持无标签形态）；
    - 单文档失败不致命（记录日志继续），整体异常由调用方兜底不阻塞审查；
    - failed 行为终态：不重试（避免反复消耗配额），其图片在门禁口径下
      仍算「未识别」，由「无法确认」兜底。
    """
    settings = get_settings()
    if not settings.image_preannotate_enabled:
        return {"docs": 0, "new": 0, "failed": 0, "skipped": "disabled"}

    from backend.agent.tools.baidu_ocr import BaiduOcrTool

    token = set_usage_context(
        dataclasses.replace(
            await _build_usage_context(session, user_id, task_id, project_id),
            seal_required=False,
        )
    )
    try:
        docs = (
            await session.execute(
                select(Document).where(
                    Document.project_id == project_id,
                    Document.status == "parsed",
                    Document.parsed_images_dir.isnot(None),
                )
            )
        ).scalars().all()

        ocr_tool = BaiduOcrTool()
        semaphore = asyncio.Semaphore(max(1, settings.image_preannotate_concurrency))
        total_new = total_failed = doc_count = 0
        for doc in docs:
            try:
                new, failed = await _annotate_document(
                    session, doc, ocr_tool, semaphore,
                    max_images=settings.image_preannotate_max_images_per_task,
                )
                total_new += new
                total_failed += failed
                doc_count += 1
            except Exception as exc:  # 单文档失败不阻塞其余文档
                logger.error("[preannotate] 文档 %s 预识别失败: %s", doc.id, exc)
        await session.commit()
        stats = {"docs": doc_count, "new": total_new, "failed": total_failed}
        logger.info("[preannotate] task=%s project=%s: %s", task_id, project_id, stats)
        return stats
    finally:
        reset_usage_context(token)
