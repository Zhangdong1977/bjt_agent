# -*- coding: utf-8 -*-
"""bidfig gantt — 实施进度横道图（甘特图）确定性渲染器。

输入 JSON（LLM 只填数据）：
    {"kind": "gantt", "title": "项目实施进度计划",
     "start": "2026-11-01", "end": "2027-04-30",          # 可选，缺省取任务/里程碑范围
     "phases":   [{"name": "阶段一 标品部署"}],             # 可选
     "tasks":    [{"name": "私有化部署", "phase": 0,
                   "start": "2026-11-01", "end": "2026-12-10"}],
     "milestones": [{"name": "M1 标品上线", "date": "2026-12-10"}]}

渲染策略（对应 2026-09 真机翻车点的原语级修复）：
- 刻度自适应：≤11 天按日、≤75 天按周、更长按月（超 26 个月按季）——短跨度按天
  出刻度挤成一团是 mermaid gantt 的真机问题，这里在渲染器层面根治；
- 中文任务名先折行/截断后落位，条内放不下放条右侧、再放不下条内截断，永不溢出；
- 色板按阶段循环取色，同一份数据渲染结果逐字节确定（可重渲、可 diff）。
"""
from __future__ import annotations

import datetime as _dt

from .primitives import (BAND, BORDER, CANVAS_W, INK, LINE, MUTED, PALETTE,
                         diamond, ellipsize, line, rect, svg_open, text_w,
                         tspans)

PAD = 16
AXIS_H = 26
BAND_H = 24
TASK_H = 27
MILE_H = 26


class BidfigSpecError(ValueError):
    """图表数据不合法（fences 层捕获后降级为占位说明，不阻断生成）。"""


def _parse_date(value, field: str, errors: list[str]) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        errors.append("%s 日期格式应为 YYYY-MM-DD（收到 %r）" % (field, value))
        return None


def validate(spec: dict) -> dict:
    """校验并规范化 gantt spec；不合法抛 BidfigSpecError（信息聚合）。"""
    if not isinstance(spec, dict):
        raise BidfigSpecError("图表数据必须是 JSON 对象")
    errors: list[str] = []

    title = str(spec.get("title") or "").strip()
    if not title:
        errors.append("title 不能为空")

    raw_tasks = spec.get("tasks")
    raw_ms = spec.get("milestones")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        if not isinstance(raw_ms, list) or not raw_ms:
            errors.append("tasks 与 milestones 至少提供一项且非空")

    phases: list[dict] = []
    raw_phases = spec.get("phases")
    if raw_phases is not None:
        if not isinstance(raw_phases, list):
            errors.append("phases 应为数组")
        else:
            for i, ph in enumerate(raw_phases):
                name = str((ph or {}).get("name") or "").strip()
                if not name:
                    errors.append("phases[%d].name 不能为空" % i)
                phases.append({"name": name})

    tasks: list[dict] = []
    for i, t in enumerate(raw_tasks or []):
        t = t if isinstance(t, dict) else {}
        name = str(t.get("name") or "").strip()
        if not name:
            errors.append("tasks[%d].name 不能为空" % i)
        start = _parse_date(t.get("start"), "tasks[%d].start" % i, errors)
        end = _parse_date(t.get("end"), "tasks[%d].end" % i, errors)
        if start and end and end < start:
            errors.append("tasks[%d] 结束日期早于开始日期" % i)
        phase_idx = -1
        if phases:
            ref = t.get("phase", 0)
            if isinstance(ref, bool):
                ref = 0
            elif isinstance(ref, int):
                if not 0 <= ref < len(phases):
                    errors.append("tasks[%d].phase=%d 超出 phases 下标范围" % (i, ref))
                else:
                    phase_idx = ref
            else:
                for j, ph in enumerate(phases):
                    if ph["name"] == str(ref):
                        phase_idx = j
                        break
                else:
                    errors.append("tasks[%d].phase=%r 找不到对应阶段" % (i, ref))
        tasks.append({"name": name, "start": start, "end": end, "phase": phase_idx})

    milestones: list[dict] = []
    for i, m in enumerate(raw_ms or []):
        m = m if isinstance(m, dict) else {}
        name = str(m.get("name") or "").strip()
        if not name:
            errors.append("milestones[%d].name 不能为空" % i)
        date = _parse_date(m.get("date"), "milestones[%d].date" % i, errors)
        milestones.append({"name": name, "date": date})

    if errors:
        raise BidfigSpecError("；".join(errors[:6]))

    bounds = [t["start"] for t in tasks if t["start"]] + [t["end"] for t in tasks if t["end"]] \
        + [m["date"] for m in milestones if m["date"]]
    for key in ("start", "end"):
        raw = spec.get(key)
        if raw in (None, ""):
            continue
        d = _parse_date(raw, key, errors)
        if d:
            bounds.append(d)
    if errors:
        raise BidfigSpecError("；".join(errors[:6]))
    t0, t1 = min(bounds), max(bounds)
    if t0 == t1:
        t1 = t0 + _dt.timedelta(days=1)
    else:
        # 轴右端留 ~3% 内边距：末端里程碑菱形/任务条不贴右边框（视觉评审抓到的收边缺陷）
        t1 = t1 + _dt.timedelta(days=max(1, round((t1 - t0).days * 0.03)))
    return {"title": title, "t0": t0, "t1": t1, "phases": phases,
            "tasks": tasks, "milestones": milestones}


def _ticks(t0: _dt.date, t1: _dt.date) -> list[tuple[_dt.date, str]]:
    span = (t1 - t0).days
    if span <= 11:
        return [(t0 + _dt.timedelta(days=i), (t0 + _dt.timedelta(days=i)).strftime("%m-%d"))
                for i in range(span + 1)]
    if span <= 75:
        first = t0 + _dt.timedelta(days=(7 - t0.weekday()) % 7)
        step = 7 if (t1 - first).days // 7 <= 14 else 14
        return [(d, d.strftime("%m-%d"))
                for d in _month_iter(first, t1, _dt.timedelta(days=step))]
    months = _month_starts(t0, t1)
    if len(months) <= 26:
        return [(d, d.strftime("%Y-%m")) for d in months]
    return [(d, "%d-Q%d" % (d.year, (d.month - 1) // 3 + 1))
            for d in months if (d.month - 1) % 3 == 0]


def _month_iter(first: _dt.date, last: _dt.date, step: _dt.timedelta):
    d = first
    while d <= last:
        yield d
        d += step


def _month_starts(t0: _dt.date, t1: _dt.date) -> list[_dt.date]:
    out, y, m = [], t0.year, t0.month
    while _dt.date(y, m, 1) <= t1:
        out.append(_dt.date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def render(spec: dict) -> str:
    v = validate(spec)
    t0, t1 = v["t0"], v["t1"]
    phases, tasks, milestones = v["phases"], v["tasks"], v["milestones"]
    total = (t1 - t0).days

    groups: list[dict] = []
    if phases:
        for idx, ph in enumerate(phases):
            groups.append({"name": ph["name"], "tasks": [t for t in tasks if t["phase"] == idx]})
        loose = [t for t in tasks if t["phase"] < 0]
        if loose:
            groups.append({"name": "其他", "tasks": loose})
    else:
        groups = [{"name": "", "tasks": tasks}]
    groups = [g for g in groups if g["tasks"]] or [{"name": "", "tasks": []}]

    labels = [t["name"] for g in groups for t in g["tasks"]] + [m["name"] for m in milestones]
    label_w = min(260.0, max(120.0, max((text_w(n, 13) for n in labels), default=120) + 8))
    x0, x1 = PAD + label_w + 8, CANVAS_W - PAD
    grid_w = x1 - x0

    def x_of(d: _dt.date) -> float:
        return x0 + (d - t0).days / total * grid_w

    ticks = _ticks(t0, t1)

    height = AXIS_H
    for gi, g in enumerate(groups):
        if g["name"]:
            height += BAND_H
        height += TASK_H * len(g["tasks"])
    if milestones:
        height += BAND_H + MILE_H * len(milestones)
    height += 6

    out = [svg_open(CANVAS_W, height, v["title"])]
    out.append(rect(PAD, 0.5, CANVAS_W - PAD * 2, height - 1, "none", BORDER, 1.0))

    # 时间轴：刻度标签 + 基线 + 贯穿网格线
    body_bottom = height - 4
    for d, label in ticks:
        tx = x_of(d)
        out.append(line(tx, AXIS_H, tx, body_bottom, LINE, 0.8))
        anchor, ax = "middle", tx
        if tx < x0 + 18:
            anchor, ax = "start", x0
        elif tx > x1 - 18:
            anchor, ax = "end", x1
        out.append(tspans(ax, 17, [label], 11, MUTED, anchor=anchor))
    out.append(line(x0, AXIS_H - 4, x1, AXIS_H - 4, BORDER, 1.0))

    y = AXIS_H
    for gi, g in enumerate(groups):
        main, tint = PALETTE[gi % len(PALETTE)]
        if g["name"]:
            out.append(rect(PAD, y, CANVAS_W - PAD * 2, BAND_H, BAND))
            out.append(rect(PAD, y, 3, BAND_H, main))
            out.append(tspans(PAD + 10, y + 16.5, [ellipsize(g["name"], 13, CANVAS_W - PAD * 2 - 20, True)], 13, INK, 700))
            y += BAND_H
        for t in g["tasks"]:
            s, e = t["start"] or t0, t["end"] or t0
            bx0, bx1 = x_of(s), max(x_of(e), x_of(s) + 4)
            by, bh = y + 4, TASK_H - 8
            out.append(tspans(PAD + 4, y + TASK_H / 2 + 4.5,
                              [ellipsize(t["name"], 13, label_w)], 13, INK))
            out.append(rect(bx0, by, bx1 - bx0, bh, tint, main, 1.0, rx=4))
            out.append(line(PAD, y + TASK_H, x1, y + TASK_H, LINE, 0.8))
            y += TASK_H

    if milestones:
        main, tint = PALETTE[2]
        out.append(rect(PAD, y, CANVAS_W - PAD * 2, BAND_H, BAND))
        out.append(rect(PAD, y, 3, BAND_H, main))
        out.append(tspans(PAD + 10, y + 16.5, ["里程碑"], 13, INK, 700))
        y += BAND_H
        for m in milestones:
            mx = x_of(m["date"] or t0)
            out.append(tspans(PAD + 4, y + MILE_H / 2 + 4.5,
                              [ellipsize(m["name"], 13, label_w)], 13, INK))
            out.append(diamond(mx, y + MILE_H / 2, 8, tint, main))
            out.append(line(PAD, y + MILE_H, x1, y + MILE_H, LINE, 0.8))
            y += MILE_H

    out.append("</svg>")
    return "".join(out)
