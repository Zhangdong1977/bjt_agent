# -*- coding: utf-8 -*-
"""bidfig seq — 系统对接/交互时序图确定性渲染器（P4 扩类）。

输入 JSON（LLM 只填数据）：
    {"kind": "seq", "title": "系统对接交互时序",
     "actors": ["投标人客户端", "评标平台", "运营后台"],
     "messages": [{"from": 0, "to": 1, "text": "上传投标文件"},
                  {"from": 1, "to": 2, "text": "解析与合规校验", "dash": true},
                  {"from": 2, "to": 2, "text": "生成审查报告"}]}

渲染策略：
- 参与方顶栏自动均分画布（≤6 个），名称折行 ≤2 行；生命线虚线贯穿；
- 消息=编号横箭头（from>to 自动反向），文字置于箭头上方、按可用空间单行截断；
- 自交互（from==to）画右侧小回环箭头，不丢弃也不报错；
- 严禁虚构的机制同其余原语：只吃结构化数据，版式全部代码定。
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from .primitives import (BORDER, CANVAS_W, INK, LINE, MUTED, PALETTE,
                         arrow_h, ellipsize, line, rect, svg_open, wrap)
from .gantt import BidfigSpecError

PAD = 16
ACT_GAP = 14
HEAD_H = 40
MSG_H = 36
LINE_H = 15


def _idx(value, n: int, field: str, errors: list[str]) -> int:
    if isinstance(value, bool):
        errors.append("%s 应为参与方下标（0-%d）" % (field, n - 1))
        return -1
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or not 0 <= value < n:
        errors.append("%s=%r 超出参与方下标范围（0-%d）" % (field, value, n - 1))
        return -1
    return value


def validate(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise BidfigSpecError("图表数据必须是 JSON 对象")
    errors: list[str] = []
    title = str(spec.get("title") or "").strip()
    if not title:
        errors.append("title 不能为空")

    raw_actors = spec.get("actors")
    if not isinstance(raw_actors, list) or not 2 <= len(raw_actors) <= 6:
        raise BidfigSpecError("actors 应为 2-6 个参与方")
    actors = []
    for i, a in enumerate(raw_actors):
        name = str(a or "").strip()
        if not name:
            errors.append("actors[%d] 不能为空" % i)
        actors.append(name)

    messages: list[dict] = []
    raw_msgs = spec.get("messages")
    if not isinstance(raw_msgs, list) or not raw_msgs:
        errors.append("messages 至少 1 项")
    for i, m in enumerate(raw_msgs[:20]):
        m = m if isinstance(m, dict) else {}
        text = str(m.get("text") or "").strip()
        if not text:
            errors.append("messages[%d].text 不能为空" % i)
        messages.append({
            "from": _idx(m.get("from"), len(actors), "messages[%d].from" % i, errors),
            "to": _idx(m.get("to"), len(actors), "messages[%d].to" % i, errors),
            "text": text[:60],
            "dash": bool(m.get("dash")),
        })
    if errors:
        raise BidfigSpecError("；".join(errors[:6]))
    return {"title": title, "actors": actors, "messages": messages}


def render(spec: dict) -> str:
    v = validate(spec)
    actors, messages = v["actors"], v["messages"]
    n = len(actors)
    act_w = (CANVAS_W - PAD * 2 - (n - 1) * ACT_GAP) / n
    centers = [PAD + i * (act_w + ACT_GAP) + act_w / 2 for i in range(n)]

    total_h = PAD + HEAD_H + 22 + len(messages) * MSG_H + PAD
    out = [svg_open(CANVAS_W, total_h, v["title"])]

    for i, name in enumerate(actors):
        main, tint = PALETTE[i % len(PALETTE)]
        x = centers[i] - act_w / 2
        out.append(rect(x, PAD, act_w, HEAD_H, tint, BORDER, 1.0))
        out.append(rect(x, PAD, act_w, 3, main))
        name_lines = wrap(name, 13, act_w - 12, bold=True, max_lines=2)
        top = PAD + (HEAD_H - LINE_H * len(name_lines)) / 2 + 13
        out.append('<g text-anchor="middle">' + "".join(
            '<text x="%.1f" y="%.1f" font-size="13" fill="%s" font-weight="700">%s</text>'
            % (centers[i], top + k * LINE_H, INK, escape(s))
            for k, s in enumerate(name_lines)) + "</g>")
        out.append(line(centers[i], PAD + HEAD_H, centers[i], total_h - PAD, LINE, 1.0, dash="4 4"))

    body_top = PAD + HEAD_H + 22
    for k, m in enumerate(messages):
        y = body_top + k * MSG_H + MSG_H - 8   # 箭头所在行
        src, dst = centers[m["from"]], centers[m["to"]]
        dash = "6 4" if m["dash"] else None
        label = "%d. %s" % (k + 1, m["text"])
        if m["from"] == m["to"]:  # 自交互：右侧小回环（视觉评审：回环线须让开标签文字）
            loop_w = min(46.0, CANVAS_W - PAD - src - 10)
            out.append(arrow_h(src, src + loop_w, y - 6, MUTED, dash))
            out.append(line(src + loop_w, y - 6, src + loop_w, y + 8, MUTED, 1.6))
            out.append(arrow_h(src + loop_w, src, y + 8, MUTED, dash))
            mid = min(src + loop_w / 2, (src + CANVAS_W - PAD) / 2)
            maxw = 2 * min(mid - PAD, CANVAS_W - PAD - mid) - 10
            label_y = y - 11
        else:
            out.append(arrow_h(src, dst, y, MUTED, dash))
            mid = (src + dst) / 2
            # 文字可适度宽于箭头跨度（相邻参与方时箭头短），但不得越出画布
            maxw = min(abs(dst - src) * 1.6, 2 * min(mid - PAD, CANVAS_W - PAD - mid) - 10)
            label_y = y - 7
        out.append('<g text-anchor="middle">'
                   '<text x="%.1f" y="%.1f" font-size="12" fill="%s">%s</text></g>'
                   % (mid, label_y, INK, escape(ellipsize(label, 12, max(maxw, 40)))))
        if k < len(messages) - 1:
            out.append(line(PAD + 6, y + MSG_H - 14, CANVAS_W - PAD - 6, y + MSG_H - 14, LINE, 0.6))

    out.append("</svg>")
    return "".join(out)
