# -*- coding: utf-8 -*-
"""bidfig bar — 成组柱状对比图确定性渲染器（P5 扩类，2026-10-24 用户反馈补充）。

输入 JSON（LLM 只填数据）：
    {"kind": "bar", "title": "关键性能指标对比", "unit": "ms",
     "groups": [{"name": "页面响应时间",
                 "bars": [{"name": "招标要求", "value": 500},
                          {"name": "我方应答", "value": 200}]},
                {"name": "并发处理能力",
                 "bars": [{"name": "招标要求", "value": 1000},
                          {"name": "我方应答", "value": 1200}]}]}

渲染策略：
- 同名柱跨组同色（“招标要求/我方应答”语义稳定），表头图例行按名去重展示；
- 纵轴上限向上取 1/2/5×10^k 的“好数”并五等分网格，杜绝 372.4 这类刻度；
- 柱顶数值标签按槽宽截断、组名折行 ≤2 行，宽度全部先算后画、永不溢出；
- value 数值口径与正文一致由 prompt 铁律兜底，这里只做几何。
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from .primitives import (BORDER, CANVAS_W, INK, LINE, MUTED, PALETTE,
                         ellipsize, line, rect, svg_open, text_w, wrap)
from .gantt import BidfigSpecError

PAD = 16
Y_LABEL_W = 64          # 纵轴刻度文字区
PLOT_H = 240            # 绘图区高
BAR_MAX = 4             # 每组柱数上限
LINE_H = 15


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
    if abs(value - round(value)) < 1e-9:
        return "%d" % round(value)
    return ("%.2f" % value).rstrip("0").rstrip(".")


def _nice_max(value: float) -> float:
    """向上取 1/2/5×10^k 的整数刻度上限。"""
    if value <= 0:
        return 100.0
    exp = 0
    scaled = value
    while scaled > 10.0:
        scaled /= 10.0
        exp += 1
    while scaled <= 1.0:
        scaled *= 10.0
        exp -= 1
    for step in (1.0, 2.0, 5.0, 10.0):
        if scaled <= step + 1e-9:
            return step * (10.0 ** exp)
    return 10.0 * (10.0 ** exp)


def validate(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise BidfigSpecError("图表数据必须是 JSON 对象")
    errors: list[str] = []
    title = str(spec.get("title") or "").strip()
    if not title:
        errors.append("title 不能为空")

    raw_groups = spec.get("groups")
    if not isinstance(raw_groups, list) or not 1 <= len(raw_groups) <= 8:
        raise BidfigSpecError("groups 应为 1-8 组")
    groups: list[dict] = []
    positive = False
    for i, g in enumerate(raw_groups):
        g = g if isinstance(g, dict) else {}
        gname = str(g.get("name") or "").strip()
        if not gname:
            errors.append("groups[%d].name 不能为空" % i)
        raw_bars = g.get("bars")
        if not isinstance(raw_bars, list) or not 1 <= len(raw_bars) <= BAR_MAX:
            errors.append("groups[%d].bars 应为 1-%d 根" % (i, BAR_MAX))
            raw_bars = []
        bars: list[dict] = []
        for j, b in enumerate(raw_bars):
            b = b if isinstance(b, dict) else {}
            bname = str(b.get("name") or "").strip()
            if not bname:
                errors.append("groups[%d].bars[%d].name 不能为空" % (i, j))
            value = _num(b.get("value"), "groups[%d].bars[%d].value" % (i, j), errors)
            if value < 0:
                errors.append("groups[%d].bars[%d].value 不能为负数" % (i, j))
            if value > 0:
                positive = True
            bars.append({"name": bname[:16], "value": max(0.0, value)})
        groups.append({"name": gname[:30], "bars": bars})
    if not positive:
        errors.append("至少需要一根 value > 0 的柱子")
    unit = str(spec.get("unit") or "").strip()[:8]
    if errors:
        raise BidfigSpecError("；".join(errors[:6]))
    return {"title": title, "groups": groups, "unit": unit}


def render(spec: dict) -> str:
    v = validate(spec)
    groups, unit = v["groups"], v["unit"]

    # 同名柱跨组同色：按首次出现顺序分配色板
    color_of: dict[str, str] = {}
    for g in groups:
        for b in g["bars"]:
            if b["name"] not in color_of:
                color_of[b["name"]] = PALETTE[len(color_of) % len(PALETTE)][0]

    top_max = max(b["value"] for g in groups for b in g["bars"])
    ymax = _nice_max(top_max)
    step = ymax / 5.0

    head_h = 18 if (unit or len(color_of) > 1) else 6
    plot_top = PAD + head_h
    plot_bottom = plot_top + PLOT_H
    label_top = plot_bottom + 14          # x 轴线下组名区
    total_h = int(label_top + 2 * LINE_H + PAD)

    plot_left = PAD + Y_LABEL_W
    plot_right = CANVAS_W - PAD - 12
    plot_w = plot_right - plot_left

    out = [svg_open(CANVAS_W, total_h, v["title"])]

    # 表头行：单位说明（左）+ 图例（同色同名去重，右侧顺排）
    head_y = PAD + 12
    if unit:
        out.append('<text x="%.1f" y="%.1f" font-size="12" fill="%s">单位：%s</text>'
                   % (plot_left, head_y, MUTED, escape(unit)))
    if len(color_of) > 1:
        lx = plot_left + (170 if unit else 0)
        for name, color in color_of.items():
            out.append('<rect x="%.1f" y="%.1f" width="12" height="12" rx="2" fill="%s" stroke="%s"/>'
                       % (lx, head_y - 10, color, BORDER))
            label = ellipsize(name, 12, 160)
            out.append('<text x="%.1f" y="%.1f" font-size="12" fill="%s">%s</text>'
                       % (lx + 17, head_y, INK, escape(label)))
            lx += 17 + text_w(label, 12) + 24

    # 网格与纵轴刻度（五等分）
    for k in range(6):
        y = plot_bottom - PLOT_H * k / 5.0
        out.append(line(plot_left, y, plot_right, y, LINE, 0.8 if k else 1.2))
        out.append('<g text-anchor="end">'
                   '<text x="%.1f" y="%.1f" font-size="11" fill="%s">%s</text></g>'
                   % (plot_left - 6, y + 4, MUTED, escape(_fmt(step * k))))

    # 组槽与柱体：柱顶数值标签、组名折行 ≤2 行居中
    slot = plot_w / len(groups)
    for gi, g in enumerate(groups):
        x0 = plot_left + gi * slot
        n = len(g["bars"])
        bar_w = max(10.0, min(56.0, (slot - 24) / max(n, 1) - 8))
        inner = bar_w * n + 8 * (n - 1)
        bx = x0 + (slot - inner) / 2.0
        for bi, b in enumerate(g["bars"]):
            h = PLOT_H * (b["value"] / ymax) if ymax else 0.0
            color = color_of.get(b["name"], PALETTE[0][0])
            rx = bx + bi * (bar_w + 8)
            out.append(rect(rx, plot_bottom - h, bar_w, max(h, 1.0), color, "#FFFFFF", 1.0))
            out.append(rect(rx, plot_bottom - h, bar_w, 2.5, color))
            value_label = ellipsize(_fmt(b["value"]), 11, slot - 10)
            out.append('<g text-anchor="middle">'
                       '<text x="%.1f" y="%.1f" font-size="11" fill="%s">%s</text></g>'
                       % (rx + bar_w / 2.0, plot_bottom - h - 5, INK, escape(value_label)))
        name_lines = wrap(g["name"], 12, slot - 14, max_lines=2)
        name_top = label_top + (0 if len(name_lines) > 1 else 7)
        out.append('<g text-anchor="middle">' + "".join(
            '<text x="%.1f" y="%.1f" font-size="12" fill="%s">%s</text>'
            % (x0 + slot / 2.0, name_top + k * LINE_H, INK, escape(s))
            for k, s in enumerate(name_lines)) + "</g>")

    out.append("</svg>")
    return "".join(out)
