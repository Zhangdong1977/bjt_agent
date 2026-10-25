# -*- coding: utf-8 -*-
"""bidfig pie — 占比构成图（环图+图例）确定性渲染器（P5 扩类，2026-10-24 用户反馈补充）。

输入 JSON（LLM 只填数据）：
    {"kind": "pie", "title": "项目团队人员结构", "unit": "人",
     "items": [{"name": "高级工程师", "value": 12},
               {"name": "中级工程师", "value": 20},
               {"name": "项目经理", "value": 3}]}

渲染策略：
- 环图居左、图例居右逐行（色块+名称+数量+占比）——中文占比标签挂扇区上必然挤压
  出血，图例行宽度充足永不溢出；扇区内只放 ≥5% 的百分比数字；
- value 是数量不是百分比（占比由渲染器计算，杜绝 LLM 算错百分比的虚构面）；
- 颜色按 items 顺序循环 PALETTE，同数据逐字节确定（可重渲、可 diff）；
- 中心留白，unit 提供时居中显示合计（如“35 人”）。
"""
from __future__ import annotations

import math
from xml.sax.saxutils import escape

from .primitives import (BORDER, CANVAS_W, INK, MUTED, PALETTE,
                         ellipsize, svg_open, text_w, tspans)
from .gantt import BidfigSpecError

PAD = 16
R_OUT = 140.0          # 环外半径
R_IN = 86.0            # 环内半径（带宽 54）
MIN_LABEL_ARC = 18.0   # 扇区圆心角 <18°（5%）不放扇区内百分比标签
ROW_H = 26             # 图例行高


def _num(value, field: str, errors: list[str]) -> float:
    if isinstance(value, bool):
        errors.append("%s 应为数值" % field)
        return -1.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    try:
        return float(text)
    except ValueError:
        errors.append("%s 应为数值（收到 %r）" % (field, value))
        return -1.0


def _fmt(value: float) -> str:
    return ("%d" % round(value)) if abs(value - round(value)) < 1e-9 else ("%.2f" % value).rstrip("0").rstrip(".")


def validate(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise BidfigSpecError("图表数据必须是 JSON 对象")
    errors: list[str] = []
    title = str(spec.get("title") or "").strip()
    if not title:
        errors.append("title 不能为空")

    raw_items = spec.get("items")
    if not isinstance(raw_items, list) or not 2 <= len(raw_items) <= 12:
        raise BidfigSpecError("items 应为 2-12 项")
    items: list[dict] = []
    for i, raw in enumerate(raw_items):
        raw = raw if isinstance(raw, dict) else {}
        name = str(raw.get("name") or "").strip()
        if not name:
            errors.append("items[%d].name 不能为空" % i)
        value = _num(raw.get("value"), "items[%d].value" % i, errors)
        if value < 0:
            errors.append("items[%d].value 不能为负数" % i)
        items.append({"name": name[:40], "value": max(0.0, value)})
    total = sum(it["value"] for it in items)
    if total <= 0:
        errors.append("items 的 value 合计必须大于 0")
    unit = str(spec.get("unit") or "").strip()[:8]
    if errors:
        raise BidfigSpecError("；".join(errors[:6]))
    return {"title": title, "items": items, "total": total, "unit": unit}


def _point(cx: float, cy: float, r: float, deg: float) -> tuple[float, float]:
    """圆心角按 12 点钟方向顺时针计（与环图阅读习惯一致）。"""
    rad = math.radians(deg)
    return cx + r * math.sin(rad), cy - r * math.cos(rad)


def _slice_path(cx: float, cy: float, a0: float, a1: float) -> str:
    """环形扇区 path：外弧顺时针 + 内弧回程，白色描边分隔相邻扇区。"""
    ox0, oy0 = _point(cx, cy, R_OUT, a0)
    ox1, oy1 = _point(cx, cy, R_OUT, a1)
    ix1, iy1 = _point(cx, cy, R_IN, a1)
    ix0, iy0 = _point(cx, cy, R_IN, a0)
    large = 1 if (a1 - a0) > 180.0 else 0
    return (
        "M %.1f,%.1f A %.1f,%.1f 0 %d 1 %.1f,%.1f "
        "L %.1f,%.1f A %.1f,%.1f 0 %d 0 %.1f,%.1f Z"
        % (ox0, oy0, R_OUT, R_OUT, large, ox1, oy1,
           ix1, iy1, R_IN, R_IN, large, ix0, iy0)
    )


def render(spec: dict) -> str:
    v = validate(spec)
    items, total = v["items"], v["total"]

    cx = PAD + R_OUT + 24
    cy = PAD + 24 + R_OUT
    legend_x = cx + R_OUT + 72
    legend_w = CANVAS_W - PAD - legend_x
    legend_h = len(items) * ROW_H + 6
    total_h = int(max(cy + R_OUT + PAD, PAD + 24 + legend_h + PAD))

    out = [svg_open(CANVAS_W, total_h, v["title"])]

    angle = 0.0
    for i, it in enumerate(items):
        main, _tint = PALETTE[i % len(PALETTE)]
        pct = it["value"] / total * 100.0
        a0, a1 = angle, angle + pct / 100.0 * 360.0
        angle = a1
        out.append('<path d="%s" fill="%s" stroke="#FFFFFF" stroke-width="1.6"/>' % (
            _slice_path(cx, cy, a0, a1), main))
        if a1 - a0 >= MIN_LABEL_ARC:
            lm = (a0 + a1) / 2.0
            lx, ly = _point(cx, cy, (R_OUT + R_IN) / 2.0, lm)
            out.append('<g text-anchor="middle">'
                       '<text x="%.1f" y="%.1f" font-size="12" fill="#FFFFFF" font-weight="600">%.1f%%</text></g>'
                       % (lx, ly + 4, pct))
        # 图例行：色块 + 名称（截断） + 数量与占比
        row_y = PAD + 24 + i * ROW_H
        out.append('<rect x="%.1f" y="%.1f" width="12" height="12" rx="2" fill="%s" stroke="%s"/>'
                   % (legend_x, row_y + 2, main, BORDER))
        value_text = "%s%s" % (_fmt(it["value"]), (" " + v["unit"]) if v["unit"] else "")
        tail = "  %s · %.1f%%" % (value_text, pct)
        name_w = max(60.0, legend_w - 14 - text_w(tail, 13) - 10)
        out.append(tspans(legend_x + 20, row_y + 12, [ellipsize(it["name"], 13, name_w)], 13, INK, 600))
        out.append('<text x="%.1f" y="%.1f" font-size="13" fill="%s">%s</text>'
                   % (CANVAS_W - PAD - text_w(tail, 13), row_y + 12, MUTED, escape(tail)))

    if v["unit"]:
        out.append('<g text-anchor="middle">'
                   '<text x="%.1f" y="%.1f" font-size="17" fill="%s" font-weight="700">%s</text>'
                   '<text x="%.1f" y="%.1f" font-size="12" fill="%s">合计</text></g>'
                   % (cx, cy - 2, INK, escape("%s %s" % (_fmt(total), v["unit"])),
                      cx, cy + 18, MUTED))

    out.append("</svg>")
    return "".join(out)
