"""Generate a structured PDF report for bid review results.

Uses reportlab's built-in CID CJK font ``STSong-Light`` so no external font
file is required — Chinese renders correctly out of the box on any platform.

The single public entry point is :func:`build_review_pdf`. It takes already-
grouped findings (groups MUST be pre-sorted by the caller — we sort here by
section label dictionary order as the canonical chapter order) and returns
PDF bytes.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.utils.time_utils import ensure_utc_aware

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# Built-in CID CJK font — no font file needed. Registered once, idempotent.
CJK_FONT = "STSong-Light"
_font_registered = False


def _ensure_font() -> None:
    global _font_registered
    if _font_registered:
        return
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(CJK_FONT))
        _font_registered = True
    except Exception:
        # Should not happen with a stock reportlab install, but don't crash
        # the whole export — fall back silently (Latin/CJK may look wrong).
        logger.exception("[pdf_export] register CJK font failed")


# ---- Palette (lightweight, print-friendly) ----------------------------------
_BRAND_RED = colors.HexColor("#D7041A")
_TEXT_DARK = colors.HexColor("#222222")
_TEXT_MUTE = colors.HexColor("#888888")
_LINE = colors.HexColor("#DDDDDD")
_BG_HEADER = colors.HexColor("#F5F5F5")
_BG_RISK = colors.HexColor("#FFF1F0")
_LINK_BLUE = colors.HexColor("#2563EB")

# 风险等级配色（严重红/重要橙/一般绿），前端 OverallReportPanel 使用同一组色值
_HEX_CRITICAL = "#C0392B"
_HEX_MAJOR = "#E67E22"
_HEX_MINOR = "#27AE60"
_SEV_HEX = {"critical": _HEX_CRITICAL, "major": _HEX_MAJOR, "minor": _HEX_MINOR}
_LEVEL_HEX = {"高": _HEX_CRITICAL, "中": _HEX_MAJOR, "低": _HEX_MINOR}

# Promo banner shown in every page header — links to the review portal.
_PROMO_TEXT = "点我立即开始检查："
_PROMO_URL = "https://check.aibjt.com:30002"


# Display timezone for human-readable timestamps in the report. ``completed_at``
# is stored as tz-aware UTC (see backend/utils/time_utils.py); render it in
# China Standard Time so the PDF matches what users see in the UI. Naive
# datetimes (e.g. the legacy ``datetime.now()`` footer fallback) are treated as
# UTC by ``ensure_utc_aware`` before conversion.
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def _severity_label(severity: str | None) -> str:
    return {
        "critical": "严重",
        "major": "重要",
        "minor": "一般",
    }.get((severity or "").lower(), "—")


def _fmt_datetime(dt: datetime | None) -> str:
    if not dt:
        return "暂无"
    # reportlab's STSong-Light is CJK; keep zh-CN formatting consistent with UI.
    # Convert from UTC (the storage timezone) to local time before formatting —
    # otherwise the printed time is 8 hours behind for CST users.
    try:
        local = ensure_utc_aware(dt)
        if local is not None:
            local = local.astimezone(LOCAL_TZ)
        return (local or dt).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(dt)


def _escape(text: object) -> str:
    """Escape XML special chars for Paragraph; None -> em dash."""
    if text is None:
        return "—"
    s = str(text)
    # Paragraph parses inline XML (<b>..</b>, &amp; etc.), so escape ampersand
    # and angle brackets in user content to avoid parse errors.
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class OutlineParagraph(Paragraph):
    """带 PDF 书签（outline 目录项）的标题段落。

    书签在 ``draw()`` 里注册——draw 只在段落最终落页的那一页执行，因此
    被 KeepTogether/Table 包裹或触发跨页重排后，书签仍指向正确页面。
    ``outline_title`` 传纯文本（不经 _escape / 内联标记转换），作为阅读器
    目录面板里显示的标题。
    """

    def __init__(
        self, text, style, *,
        outline_title: str, outline_level: int, closed: bool = False,
    ):
        super().__init__(text, style)
        self._outline_title = outline_title
        self._outline_level = outline_level
        self._outline_closed = closed

    def draw(self):
        # 每次绘制生成新 key：split 出的副本会复用实例属性，同一 key 的
        # 目标页会互相覆盖
        key = uuid4().hex
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(
            self._outline_title, key, self._outline_level, self._outline_closed,
        )
        super().draw()


def build_review_pdf(
    project_name: str,
    task_completed_at: datetime | None,
    summary: dict,
    groups: list[dict],
    *,
    overall_report: dict | None = None,
    generated_at: datetime | None = None,
) -> bytes:
    """Render a structured bid-review report to PDF bytes.

    Args:
        project_name: Project name shown in the header.
        task_completed_at: When the review task finished.
        summary: ``{category_count, check_item_count, risk_item_count}``.
        groups: Each ``dict`` carries ``label`` (section title, already
            stripped of ``.md``), ``is_compliant``, ``non_compliant_count``
            and ``findings`` (list of ReviewResult-like objects/attrs).
            Groups are sorted by ``label`` dictionary order inside.
        overall_report: Optional overall report dict (see
            ``backend/agent/report_agent.py::assemble_report``). When present
            the PDF opens with the overall summary (rating, severity
            distribution, critical/major/minor sections, score items) and the
            per-category details become an appendix; when absent the legacy
            layout is kept.
        generated_at: Override "exported at" timestamp (UTC now by default).

    Returns:
        PDF file as bytes.
    """
    _ensure_font()

    gen_at = generated_at or datetime.now()
    buffer = io.BytesIO()

    page_w, page_h = A4
    margin = 18 * mm
    frame = Frame(
        margin, margin + 12 * mm,
        page_w - 2 * margin, page_h - 2 * margin - 24 * mm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        id="normal",
    )

    # ---- shared styles ----
    title_style = ParagraphStyle(
        "Title", fontName=CJK_FONT, fontSize=22,
        leading=28, alignment=TA_CENTER, textColor=_BRAND_RED, spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", fontName=CJK_FONT, fontSize=11, leading=16,
        alignment=TA_CENTER, textColor=_TEXT_MUTE, spaceAfter=2,
    )
    h1_style = ParagraphStyle(
        "H1", fontName=CJK_FONT, fontSize=15, leading=20, spaceBefore=14,
        spaceAfter=6, textColor=_BRAND_RED,
    )
    normal_style = ParagraphStyle(
        "Normal", fontName=CJK_FONT, fontSize=10, leading=15, spaceAfter=2,
        textColor=_TEXT_DARK,
    )
    cell_label_style = ParagraphStyle(
        "CellLabel", fontName=CJK_FONT, fontSize=9.5, leading=14,
        textColor=_TEXT_MUTE,
    )
    cell_value_style = ParagraphStyle(
        "CellValue", fontName=CJK_FONT, fontSize=9.5, leading=14,
        textColor=_TEXT_DARK,
    )
    disclaimer_style = ParagraphStyle(
        "Disclaimer", fontName=CJK_FONT, fontSize=9, leading=13,
        textColor=_TEXT_MUTE, spaceBefore=10, spaceAfter=4,
    )
    empty_style = ParagraphStyle(
        "Empty", fontName=CJK_FONT, fontSize=12, leading=18, alignment=TA_CENTER,
        textColor=_TEXT_MUTE, spaceBefore=40,
    )

    def _on_page(canvas, doc):
        canvas.saveState()

        # 打开即显示书签目录面板（文档级 PageMode=UseOutlines，幂等）
        canvas.showOutline()

        # ---- Header: promo banner (clickable), mirrored on every page ----
        header_text = _PROMO_TEXT + _PROMO_URL
        canvas.setFont(CJK_FONT, 8)
        canvas.setFillColor(_LINK_BLUE)
        canvas.drawCentredString(page_w / 2.0, page_h - margin + 2, header_text)
        # Make the drawn text a clickable hyperlink rectangle.
        tw = pdfmetrics.stringWidth(header_text, CJK_FONT, 8)
        hx = page_w / 2.0 - tw / 2.0
        hy = page_h - margin + 2
        canvas.linkURL(
            _PROMO_URL,
            (hx - 2, hy - 2, hx + tw + 2, hy + 8),
            relative=0,
        )
        canvas.setStrokeColor(_LINE)
        canvas.setLineWidth(0.5)
        canvas.line(margin, page_h - margin - 6, page_w - margin, page_h - margin - 6)

        # ---- Footer ----
        canvas.setFont(CJK_FONT, 8)
        canvas.setFillColor(_TEXT_MUTE)
        canvas.drawString(
            margin, margin - 2,
            f"导出于 {_fmt_datetime(gen_at)}",
        )
        canvas.drawRightString(
            page_w - margin, margin - 2,
            f"第 {doc.page} 页",
        )
        canvas.setStrokeColor(_LINE)
        canvas.setLineWidth(0.5)
        canvas.line(margin, margin + 6, page_w - margin, margin + 6)
        canvas.restoreState()

    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=margin,
        title=f"标书审查报告 - {project_name}",
    )
    doc.addPageTemplates([
        PageTemplate(id="main", frames=[frame], onPage=_on_page),
    ])

    story: list[Any] = []

    # ---- Title block ----
    story.append(Paragraph("标书审查报告", title_style))
    story.append(Paragraph(
        f"项目名称：{_escape(project_name)}", subtitle_style,
    ))
    story.append(Paragraph(
        f"审查完成时间：{_fmt_datetime(task_completed_at)}", subtitle_style,
    ))
    story.append(Spacer(1, 8))

    # ---- Summary (3-col) ----
    cat = summary.get("category_count", 0)
    total = summary.get("check_item_count", 0)
    risk = summary.get("risk_item_count", 0)
    summary_tbl = Table(
        [[
            Paragraph(f"<b>{cat}</b> 检查大类", cell_value_style),
            Paragraph(f"<b>{total}</b> 检查项总数", cell_value_style),
            Paragraph(f"<b>{risk}</b> 风险项总数", cell_value_style),
        ]],
        colWidths=[(page_w - 2 * margin) / 3.0] * 3,
    )
    summary_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _BG_HEADER),
        ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, _LINE),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(summary_tbl)

    # ---- Overall report: rating + severity distribution (overview) ----
    if overall_report:
        rej = overall_report.get("rejection_risk") or {}
        level = str(rej.get("level") or "低")
        level_hex = _LEVEL_HEX.get(level, "#222222")
        reason = _md_inline(rej.get("reason"))
        story.append(Paragraph(
            f"废标风险评级：<font color='{level_hex}'><b>{_escape(level)}</b></font>"
            + (f"　—　{reason}" if reason else ""),
            ParagraphStyle(
                "LevelLine", parent=normal_style, fontSize=11, leading=16,
                spaceBefore=8,
            ),
        ))
        dist = (overall_report.get("summary") or {}).get("severity_dist") or {}
        story.append(Paragraph(
            "风险等级分布："
            f"<font color='{_HEX_CRITICAL}'><b>严重 {dist.get('critical', 0)} 项</b></font>　"
            f"<font color='{_HEX_MAJOR}'><b>重要 {dist.get('major', 0)} 项</b></font>　"
            f"<font color='{_HEX_MINOR}'><b>一般 {dist.get('minor', 0)} 项</b></font>",
            ParagraphStyle("DistLine", parent=normal_style, fontSize=11, leading=16),
        ))
        failed = (overall_report.get("summary") or {}).get("failed_categories") or []
        if failed:
            story.append(Paragraph(
                f"提示：以下大类子检查未成功，结果可能不完整：{'、'.join(_escape(x) for x in failed)}",
                disclaimer_style,
            ))

    # ---- Disclaimer ----
    story.append(Paragraph(
        "免责声明：检查结果由大模型生成，仅供参考，请谨慎判别！本结果不可作为最终判定是否废标的依据，最终结果以专家实际判别为准。",
        disclaimer_style,
    ))

    # ---- Overall report: risk sections + score items ----
    if overall_report:
        if overall_report.get("degraded"):
            story.append(Paragraph(
                "提示：本次总体报告的精简描述生成失败，已降级为原始结论摘录。",
                disclaimer_style,
            ))

        section_specs = [
            ("critical", "一、严重风险", _HEX_CRITICAL),
            ("major", "二、重要风险", _HEX_MAJOR),
            ("minor", "三、一般风险", _HEX_MINOR),
        ]
        sections = overall_report.get("risk_sections") or {}
        for sev, title, hexcolor in section_specs:
            head_style = ParagraphStyle(
                f"H2_{sev}", fontName=CJK_FONT, fontSize=14, leading=19,
                spaceBefore=12, spaceAfter=4, textColor=colors.HexColor(hexcolor),
            )
            story.append(OutlineParagraph(
                title, head_style, outline_title=title, outline_level=0,
            ))
            entries = sections.get(sev) or []
            if not entries:
                story.append(Paragraph("无。", normal_style))
                continue
            for e in entries:
                head = (
                    f"<font color='{hexcolor}'><b>"
                    f"{_escape(e.get('rule_doc'))}（{e.get('count')} 项）"
                    f"</b></font>"
                )
                if sev == "critical" and e.get("rejection_related"):
                    head += (
                        f"　<font color='{_HEX_CRITICAL}'><b>〔涉及废标条款〕</b></font>"
                    )
                story.append(Paragraph(head, normal_style))
                story.append(Paragraph(_md_inline(e.get("summary") or "—"), normal_style))

        score_items = overall_report.get("score_items") or []
        if score_items:
            story.append(OutlineParagraph(
                "四、评分项得分摘要", h1_style,
                outline_title="四、评分项得分摘要", outline_level=0,
            ))
            head_row = [
                Paragraph("<b>评分项</b>", cell_value_style),
                Paragraph("<b>满分</b>", cell_value_style),
                Paragraph("<b>预估得分</b>", cell_value_style),
                Paragraph("<b>说明</b>", cell_value_style),
            ]
            rows: list[list[Any]] = [head_row]
            for it in score_items:
                rows.append([
                    Paragraph(_escape(it.get("name") or it.get("code")), cell_value_style),
                    Paragraph(_fmt_score(it.get("full_score")), cell_value_style),
                    Paragraph(_fmt_score(it.get("estimated_score")), cell_value_style),
                    Paragraph(_md_inline(it.get("note") or "—"), cell_value_style),
                ])
            content_w = page_w - 2 * margin
            score_tbl = Table(
                rows, colWidths=[content_w * 0.30, content_w * 0.12, content_w * 0.14, content_w * 0.44],
            )
            score_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), _BG_HEADER),
                ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (2, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(score_tbl)

        story.append(OutlineParagraph(
            "附录：各大类检查明细", h1_style,
            outline_title="附录：各大类检查明细", outline_level=0,
        ))

    # ---- Body: one section per check category (dict order) ----
    sorted_groups = sorted(
        groups,
        key=lambda g: (g.get("label") or "").lower(),
    )
    # 有总体报告时各大类是"附录"的下级书签，旧版式里则是顶级章节
    cat_outline_level = 1 if overall_report else 0

    if not sorted_groups:
        story.append(Paragraph("暂无审查结果。", empty_style))

    for g in sorted_groups:
        label = g.get("label") or "未分类"
        is_compliant = bool(g.get("is_compliant"))
        non_compliant = int(g.get("non_compliant_count") or 0)
        findings = g.get("findings") or []

        section_flow: list[Any] = []
        section_flow.append(OutlineParagraph(
            _escape(label), h1_style,
            outline_title=label, outline_level=cat_outline_level, closed=True,
        ))
        status_text = (
            "状态：全部合规" if is_compliant
            else f"状态：存在风险项（{non_compliant} 个）"
        )
        section_flow.append(Paragraph(status_text, normal_style))

        if not findings:
            section_flow.append(Paragraph("本大项下无检查明细。", normal_style))
        else:
            for idx, f in enumerate(findings, start=1):
                card = _finding_card(
                    f, idx,
                    cell_label_style, cell_value_style,
                    page_w - 2 * margin,
                    outline_level=cat_outline_level + 1,
                )
                section_flow.append(Spacer(1, 4))
                section_flow.append(KeepTogether(card))

        # Group header + at least its first card stay together where possible.
        story.append(Spacer(1, 6))
        # KeepTogether the heading + status to avoid orphaned section title.
        head_block = KeepTogether(section_flow[:2])
        story.append(head_block)
        story.extend(section_flow[2:])

    doc.build(story)
    return buffer.getvalue()


def _fmt_score(value: Any) -> str:
    """Format a score number; None / unparsable -> em dash."""
    if value is None:
        return "—"
    try:
        f = float(value)
        return f"{f:g}"
    except (TypeError, ValueError):
        return _escape(value)


def _finding_card(
    finding: Any,
    idx: int,
    label_style: ParagraphStyle,
    value_style: ParagraphStyle,
    content_width: float,
    outline_level: int | None = None,
) -> Table:
    """Render one finding as a single-column card (label/value rows).

    ``outline_level`` 非空时卡片头行同时注册一条检查项级 PDF 书签。
    """
    check_item = _get(finding, "check_item_name") or _get(finding, "requirement_key") or "—"
    is_compliant = bool(_get(finding, "is_compliant"))
    severity = _get(finding, "severity")
    page = _get(finding, "location_page")
    line = _get(finding, "location_line")
    requirement = _get(finding, "requirement_content")
    bid = _get(finding, "bid_content")
    explanation = _get(finding, "explanation")
    suggestion = _get(finding, "suggestion")

    compliance_text = "合规" if is_compliant else "不合规"
    header = (
        f"检查项 {_escape(idx)}：{_escape(check_item)}"
        f"　|　合规性：{_escape(compliance_text)}"
        + ("" if is_compliant else f"（{_severity_label(severity)}）")
    )
    if outline_level is not None:
        header_para: Paragraph = OutlineParagraph(
            header, value_style,
            outline_title=f"检查项 {idx}：{check_item}",
            outline_level=outline_level,
        )
    else:
        header_para = Paragraph(header, value_style)

    rows: list[list[Any]] = [[header_para]]

    # Location row
    if page is not None or line is not None:
        loc_parts = []
        if page is not None:
            loc_parts.append(f"第 {_escape(page)} 页")
        if line is not None:
            loc_parts.append(f"第 {_escape(line)} 行")
        rows.append([Paragraph(
            f"<b>位置</b>：{' '.join(loc_parts)}", value_style,
        )])

    if requirement:
        rows.extend(_md_kv_rows("依据要求", requirement, value_style, content_width))
    if bid:
        rows.extend(_md_kv_rows("投标内容", bid, value_style, content_width))
    if explanation:
        rows.extend(_md_kv_rows("问题描述", explanation, value_style, content_width))
    if suggestion:
        rows.extend(_md_kv_rows("修改建议", suggestion, value_style, content_width))

    tbl = Table(rows, colWidths=[content_width])
    style = TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, _LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        # Header row tinted; risk cards tinted red.
        ("BACKGROUND", (0, 0), (0, 0),
         _BG_RISK if not is_compliant else _BG_HEADER),
    ])
    tbl.setStyle(style)
    return tbl


def _chunk_text(text: str, limit: int = 1200) -> list[str]:
    """Split over-long text into page-safe chunks (each renders well below one frame).

    Single table rows taller than one page frame raise reportlab LayoutError
    (rows are atomic and cannot split) — seen with A009 findings whose
    ``requirement_content`` embeds the full rejection-clause list (~2700 chars
    ≈ 900pt tall). Splitting into ~1200-char chunks keeps every row ≈30 lines
    and lets the table split at row boundaries.
    """
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return chunks


# ---- 轻量 Markdown 渲染（findings 字段是 md 文本，PDF 里不能裸输出标记） ----
# 只覆盖子 agent 输出里实际出现的语法：加粗/斜体/行内代码/删除线/链接、
# 标题、无序/有序列表、代码块、表格。分块行渲染，天然兼容超长字段分块。

# 嵌套 md 表格每块的行数上限（防止单块行高超过页框，同 _chunk_text 的动机）
_MD_TABLE_CHUNK_ROWS = 12


def _md_inline(text: str) -> str:
    """Markdown 行内标记 → reportlab 段落标记（先转义，用户内容不会注入标签）."""
    t = _escape(text)
    t = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"__(.+?)__", r"<b>\1</b>", t)

    def _italic(m: re.Match) -> str:
        inner = m.group(1)
        # 首尾必须是字母/汉字才视为强调，避免把 "2*3 与 4*6" 这类算式误转斜体
        if inner and re.match(r"[A-Za-z\u4e00-\u9fff]", inner) and re.search(r"[A-Za-z\u4e00-\u9fff]$", inner):
            return f"<i>{inner}</i>"
        return m.group(0)

    t = re.sub(r"\*([^*\n]{1,80}?)\*", _italic, t)
    t = re.sub(r"~~(.+?)~~", r"<strike>\1</strike>", t)
    t = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1（\2）", t)
    return t


def _md_blocks(text: str) -> list[tuple[str, Any]]:
    """把 md 文本切成 (类型, 内容) 块：p/h/li/num/code/table."""
    lines = text.splitlines()
    blocks: list[tuple[str, Any]] = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s or re.fullmatch(r"-{3,}|\*{3,}", s):
            i += 1
            continue
        if s.startswith("|"):
            j = i
            tbl: list[str] = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                tbl.append(lines[j].strip())
                j += 1
            blocks.append(("table", tbl))
            i = j
            continue
        if s.startswith("```"):
            j = i + 1
            code: list[str] = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                code.append(lines[j])
                j += 1
            blocks.append(("code", "\n".join(code)))
            i = j + 1
            continue
        m = re.match(r"^#{1,6}\s+(.*)$", s)
        if m:
            blocks.append(("h", m.group(1)))
            i += 1
            continue
        if re.match(r"^[-*+]\s+\S", s):
            blocks.append(("li", re.sub(r"^[-*+]\s+", "", s)))
            i += 1
            continue
        m = re.match(r"^(\d+)[.、)）]\s*(\S.*)$", s)
        if m:
            blocks.append(("num", f"{m.group(1)}. {m.group(2)}"))
            i += 1
            continue
        blocks.append(("p", s))
        i += 1
    return blocks


def _md_table_rows(
    tbl_lines: list[str],
    value_style: ParagraphStyle,
    content_width: float,
) -> list[list[Any]]:
    """md 表格 → 嵌套 reportlab Table（按行分块防超页），每块作为卡片的一行."""
    data: list[list[str]] = []
    for ln in tbl_lines:
        if re.fullmatch(r"\|[\s:\-|]+\|?", ln):
            continue  # 对齐分隔行
        data.append([c.strip() for c in ln.strip().strip("|").split("|")])
    if not data:
        return []
    ncols = max(len(r) for r in data)
    cell_style = ParagraphStyle(
        "MdCell", parent=value_style, fontSize=8.5, leading=12,
    )
    avail = max(content_width - 16, 60)  # 扣掉卡片单元格左右 padding
    col_w = [avail / ncols] * ncols
    out: list[list[Any]] = []
    for start in range(0, len(data), _MD_TABLE_CHUNK_ROWS):
        chunk = data[start:start + _MD_TABLE_CHUNK_ROWS]
        tbl_rows: list[list[Any]] = []
        for ri, r in enumerate(chunk):
            cells = r + [""] * (ncols - len(r))
            tbl_rows.append([
                Paragraph(
                    (f"<b>{_md_inline(c)}</b>" if start == 0 and ri == 0 else _md_inline(c)) or " ",
                    cell_style,
                )
                for c in cells
            ])
        t = Table(tbl_rows, colWidths=col_w)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _BG_HEADER if start == 0 else colors.white),
            ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, _LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        out.append([t])
    return out


def _md_block_rows(
    blocks: list[tuple[str, Any]],
    value_style: ParagraphStyle,
    content_width: float,
) -> list[list[Any]]:
    list_style = ParagraphStyle(
        "MdList", parent=value_style, leftIndent=14, firstLineIndent=-14,
    )
    rows: list[list[Any]] = []
    for kind, content in blocks:
        if kind == "p":
            for chunk in _chunk_text(content):
                rows.append([Paragraph(_md_inline(chunk), value_style)])
        elif kind == "h":
            rows.append([Paragraph(f"<b>{_md_inline(content)}</b>", value_style)])
        elif kind == "li":
            rows.append([Paragraph(f"• {_md_inline(content)}", list_style)])
        elif kind == "num":
            rows.append([Paragraph(_md_inline(content), list_style)])
        elif kind == "code":
            code_lines = content.splitlines() or [content]
            for cs in range(0, len(code_lines), 20):
                part = code_lines[cs:cs + 20]
                html = "<br/>".join(_escape(l) or "&nbsp;" for l in part)
                rows.append([
                    Paragraph(f'<font face="Courier">{html}</font>', value_style),
                ])
        elif kind == "table":
            rows.extend(_md_table_rows(content, value_style, content_width))
    return rows


def _md_kv_rows(
    label: str,
    value: object,
    value_style: ParagraphStyle,
    content_width: float,
) -> list[list[Any]]:
    """md 字段渲染入口：单一普通段落走原分块路径（标签内联），多块走块渲染."""
    text = str(value or "")
    if not text.strip():
        return []
    blocks = _md_blocks(text)
    if len(blocks) == 1 and blocks[0][0] == "p":
        chunks = _chunk_text(blocks[0][1])
        if not chunks:
            return []
        rows = [[
            Paragraph(f"<b>{_escape(label)}</b>：{_md_inline(chunks[0])}", value_style),
        ]]
        for chunk in chunks[1:]:
            rows.append([Paragraph(_md_inline(chunk), value_style)])
        return rows
    rows = [[Paragraph(f"<b>{_escape(label)}</b>：", value_style)]]
    rows.extend(_md_block_rows(blocks, value_style, content_width))
    return rows


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute-or-item accessor: works for ORM models and plain dicts."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
