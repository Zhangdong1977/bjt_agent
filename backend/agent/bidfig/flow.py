# -*- coding: utf-8 -*-
"""bidfig flow — 实施/服务/应急处理流程图确定性渲染器（P4 扩类）。

输入 JSON（LLM 只填数据）：
    {"kind": "flow", "title": "项目实施与验收流程",
     "phases": [{"name": "准备阶段",
                 "steps": [{"name": "需求调研", "note": "对接各业务部门"}]}]}

也接受无阶段直给步骤（单列竖排）：
    {"kind": "flow", "title": "故障响应流程",
     "steps": [{"name": "受理报修"}, {"name": "分级派单", "note": "15 分钟内"}]}

渲染策略：
- 阶段=列（≤5 列），列间在阶段标题带高度画水平箭头表达流转方向；列内步骤竖排、
  盒间竖直箭头——覆盖标书里"阶段→步骤"与纯线性两类高频流程；
- 步骤名自动折行（≤2 行）、要点单行截断，文字永不溢出盒；单列（无阶段）限宽
  560 居中，避免 1100 宽画布里一根孤零零的细长链。
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from .primitives import (BORDER, CANVAS_W, INK, LINE_H, MUTED, PALETTE,
                         arrow_h, arrow_v, ellipsize, rect, svg_open, wrap)
from .gantt import BidfigSpecError

PAD = 16
COL_GAP = 34
HEAD_H = 34
BOX_GAP = 22
MAX_COL_W = 560.0


def _clean_steps(raw, where: str, errors: list[str]) -> list[dict]:
    steps: list[dict] = []
    if not isinstance(raw, list) or not raw:
        errors.append("%s.steps 至少 1 项" % where)
        return steps
    for i, s in enumerate(raw[:12]):
        s = s if isinstance(s, dict) else {}
        name = str(s.get("name") or "").strip()
        if not name:
            errors.append("%s.steps[%d].name 不能为空" % (where, i))
        note = str(s.get("note") or "").strip()[:60]
        steps.append({"name": name, "note": note})
    return steps


def validate(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise BidfigSpecError("图表数据必须是 JSON 对象")
    errors: list[str] = []
    title = str(spec.get("title") or "").strip()
    if not title:
        errors.append("title 不能为空")

    phases: list[dict] = []
    raw_phases = spec.get("phases")
    if isinstance(raw_phases, list) and raw_phases:
        if len(raw_phases) > 5:
            errors.append("phases 最多 5 个阶段列")
        for i, ph in enumerate(raw_phases[:5]):
            ph = ph if isinstance(ph, dict) else {}
            name = str(ph.get("name") or "").strip()
            if not name and len(raw_phases) > 1:
                errors.append("phases[%d].name 不能为空（多阶段时必须命名）" % i)
            phases.append({"name": name, "steps": _clean_steps(ph.get("steps"), "phases[%d]" % i, errors)})
    else:
        phases = [{"name": "", "steps": _clean_steps(spec.get("steps"), "", errors)}]

    if errors:
        raise BidfigSpecError("；".join(errors[:6]))
    return {"title": title, "phases": phases}


def render(spec: dict) -> str:
    v = validate(spec)
    phases = v["phases"]
    n = len(phases)
    area_w = CANVAS_W - PAD * 2
    if n == 1:
        col_w = min(MAX_COL_W, area_w)
        col_xs = [PAD + (area_w - col_w) / 2]
    else:
        col_w = (area_w - (n - 1) * COL_GAP) / n
        col_xs = [PAD + i * (col_w + COL_GAP) for i in range(n)]

    named = any(ph["name"] for ph in phases)
    # 每列统一盒高（同列对齐）：名称折行 ≤2、要点折行 ≤2（视觉评审：单行硬截断会句中腰斩）
    layout: list[dict] = []
    for ph in phases:
        boxes = []
        for s in ph["steps"]:
            boxes.append({
                "name_lines": wrap(s["name"], 13, col_w - 16, bold=True, max_lines=2),
                "note_lines": wrap(s["note"], 11, col_w - 16, max_lines=2) if s["note"] else [],
            })
        box_h = max(10 + LINE_H * 2 + 10, max(
            10 + LINE_H * len(b["name_lines"])
            + (4 + 14 * len(b["note_lines"]) if b["note_lines"] else 0) + 10
            for b in boxes))
        body_h = len(boxes) * box_h + BOX_GAP * (len(boxes) - 1)
        layout.append({"boxes": boxes, "box_h": box_h, "body_h": body_h})

    total_h = PAD + (HEAD_H + 16 if named else 0) + max(item["body_h"] for item in layout) + PAD
    out = [svg_open(CANVAS_W, total_h, v["title"])]

    for i, (ph, item) in enumerate(zip(phases, layout)):
        main, tint = PALETTE[i % len(PALETTE)]
        x = col_xs[i]
        top = PAD
        if named:
            out.append(rect(x, top, col_w, HEAD_H, tint))
            out.append(rect(x, top, 3, HEAD_H, main))
            out.append('<g text-anchor="middle">'
                       '<text x="%.1f" y="%.1f" font-size="13" fill="%s" font-weight="700">%s</text>'
                       "</g>"
                       % (x + col_w / 2, top + HEAD_H / 2 + 4.5, INK,
                          escape(ellipsize(ph["name"] or "流程步骤", 13, col_w - 14, True))))
            if i < n - 1:  # 列间流转箭头（阶段标题带高度）
                out.append(arrow_h(x + col_w + 3, col_xs[i + 1] - 3, top + HEAD_H / 2, MUTED))
            top += HEAD_H + 16
        for k, b in enumerate(item["boxes"]):
            by = top + k * (item["box_h"] + BOX_GAP)
            out.append(rect(x, by, col_w, item["box_h"], "#FFFFFF", BORDER, 1.0))
            out.append(rect(x, by, col_w, 3, main))
            content_h = LINE_H * len(b["name_lines"]) \
                + (4 + 14 * len(b["note_lines"]) if b["note_lines"] else 0)
            base = by + (item["box_h"] - content_h) / 2 + 13
            out.append('<g text-anchor="middle">' + "".join(
                '<text x="%.1f" y="%.1f" font-size="13" fill="%s" font-weight="700">%s</text>'
                % (x + col_w / 2, base + j * LINE_H, INK, escape(s2))
                for j, s2 in enumerate(b["name_lines"])) + "</g>")
            note_top = base + len(b["name_lines"]) * LINE_H + 4
            if b["note_lines"]:
                out.append('<g text-anchor="middle">' + "".join(
                    '<text x="%.1f" y="%.1f" font-size="11" fill="%s">%s</text>'
                    % (x + col_w / 2, note_top + j * 14, MUTED, escape(s2))
                    for j, s2 in enumerate(b["note_lines"])) + "</g>")
            if k < len(item["boxes"]) - 1:
                out.append(arrow_v(x + col_w / 2, by + item["box_h"] + 2,
                                   by + item["box_h"] + BOX_GAP - 2, MUTED))

    out.append("</svg>")
    return "".join(out)
