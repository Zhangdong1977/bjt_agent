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
from datetime import datetime
from typing import Any

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

# Promo banner shown in every page header — links to the review portal.
_PROMO_TEXT = "点我立即开始检查："
_PROMO_URL = "https://check.aibjt.com:30002"


def _severity_label(severity: str | None) -> str:
    return {
        "critical": "严重",
        "major": "重要",
        "minor": "次要",
    }.get((severity or "").lower(), "—")


def _fmt_datetime(dt: datetime | None) -> str:
    if not dt:
        return "暂无"
    # reportlab's STSong-Light is CJK; keep zh-CN formatting consistent with UI.
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
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


def build_review_pdf(
    project_name: str,
    task_completed_at: datetime | None,
    summary: dict,
    groups: list[dict],
    *,
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

    # ---- Disclaimer ----
    story.append(Paragraph(
        "免责声明：检查结果由大模型生成，仅供参考，请谨慎判别！本结果不可作为最终判定是否废标的依据，最终结果以专家实际判别为准。",
        disclaimer_style,
    ))

    # ---- Body: one section per check category (dict order) ----
    sorted_groups = sorted(
        groups,
        key=lambda g: (g.get("label") or "").lower(),
    )

    if not sorted_groups:
        story.append(Paragraph("暂无审查结果。", empty_style))

    for g in sorted_groups:
        label = g.get("label") or "未分类"
        is_compliant = bool(g.get("is_compliant"))
        non_compliant = int(g.get("non_compliant_count") or 0)
        findings = g.get("findings") or []

        section_flow: list[Any] = []
        section_flow.append(Paragraph(_escape(label), h1_style))
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


def _finding_card(
    finding: Any,
    idx: int,
    label_style: ParagraphStyle,
    value_style: ParagraphStyle,
    content_width: float,
) -> Table:
    """Render one finding as a single-column card (label/value rows)."""
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

    rows: list[list[Any]] = [[Paragraph(header, value_style)]]

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
        rows.append(_kv_row("依据要求", requirement, value_style))
    if bid:
        rows.append(_kv_row("投标内容", bid, value_style))
    if explanation:
        rows.append(_kv_row("问题描述", explanation, value_style))
    if suggestion:
        rows.append(_kv_row("修改建议", suggestion, value_style))

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


def _kv_row(label: str, value: object, value_style: ParagraphStyle) -> list[Any]:
    text = f"<b>{_escape(label)}</b>：{_escape(value)}"
    return [Paragraph(text, value_style)]


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute-or-item accessor: works for ORM models and plain dicts."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
