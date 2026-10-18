# -*- coding: utf-8 -*-
"""bidfig arch — 系统/组织架构分层图确定性渲染器。

输入 JSON（LLM 只填数据）：
    {"kind": "arch", "title": "系统总体架构",
     "layers": [{"name": "展现层",
                 "modules": [{"name": "标书编制客户端", "sub": ["Word 插件", "Web 端"]}]}],
     "side": [{"name": "安全体系", "items": ["传输加密", "权限审计"]}]}

渲染策略：
- 层=左侧竖标签+右侧模块网格，自动按宽度折列（每格 ≥158px），模块盒带层色顶条；
- 模块名自动折行（≤2 行）、要点单行截断，文字永不溢出盒；
- 层间画三支下行箭头表达调用方向；侧栏（安全/运维体系等）最多 3 条。
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from .primitives import (BORDER, CANVAS_W, INK, LINE_H, MUTED, PALETTE,
                         arrow_v, ellipsize, rect, svg_open, wrap)
from .gantt import BidfigSpecError

PAD = 16
TAB_W = 88
GAP = 10
LAYER_GAP = 20
SIDE_W = 108
SIDE_GAP = 12
MIN_BOX_W = 158


def validate(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise BidfigSpecError("图表数据必须是 JSON 对象")
    errors: list[str] = []
    title = str(spec.get("title") or "").strip()
    if not title:
        errors.append("title 不能为空")

    raw_layers = spec.get("layers")
    if not isinstance(raw_layers, list) or not raw_layers:
        raise BidfigSpecError("layers 不能为空")
    layers: list[dict] = []
    for i, layer in enumerate(raw_layers):
        layer = layer if isinstance(layer, dict) else {}
        name = str(layer.get("name") or "").strip()
        if not name:
            errors.append("layers[%d].name 不能为空" % i)
        raw_modules = layer.get("modules")
        if not isinstance(raw_modules, list) or not raw_modules:
            errors.append("layers[%d].modules 至少 1 项" % i)
            modules = []
        else:
            modules = []
            for j, m in enumerate(raw_modules[:12]):
                m = m if isinstance(m, dict) else {}
                mname = str(m.get("name") or "").strip()
                if not mname:
                    errors.append("layers[%d].modules[%d].name 不能为空" % (i, j))
                subs = [str(s).strip() for s in (m.get("sub") or []) if str(s).strip()][:4]
                modules.append({"name": mname, "sub": subs})
        layers.append({"name": name, "modules": modules})
    if errors:
        raise BidfigSpecError("；".join(errors[:6]))

    side: list[dict] = []
    raw_side = spec.get("side")
    if raw_side is not None:
        if not isinstance(raw_side, list):
            errors.append("side 应为数组")
        elif len(raw_side) > 3:
            errors.append("side 最多 3 条")
        else:
            for i, s in enumerate(raw_side):
                s = s if isinstance(s, dict) else {}
                sname = str(s.get("name") or "").strip()
                if not sname:
                    errors.append("side[%d].name 不能为空" % i)
                items = [str(x).strip() for x in (s.get("items") or []) if str(x).strip()][:8]
                side.append({"name": sname, "items": items})
    if errors:
        raise BidfigSpecError("；".join(errors[:6]))
    return {"title": title, "layers": layers, "side": side}


def render(spec: dict) -> str:
    v = validate(spec)
    layers, side = v["layers"], v["side"]
    x0 = PAD
    main_x1 = CANVAS_W - PAD - len(side) * (SIDE_W + SIDE_GAP)
    area_x0 = x0 + TAB_W + GAP
    area_w = main_x1 - area_x0

    layout: list[dict] = []
    for layer in layers:
        mods = layer["modules"]
        cols = max(1, int(area_w // MIN_BOX_W))
        cols = min(cols, len(mods))
        rows = -(-len(mods) // cols)
        box_w = (area_w - (cols - 1) * GAP) / cols
        boxes = []
        for m in mods:
            name_lines = wrap(m["name"], 13, box_w - 12, bold=True, max_lines=2)
            boxes.append({"name_lines": name_lines, "sub": m["sub"]})
        box_h = max(
            14 + LINE_H * (len(b["name_lines"]) + len(b["sub"])) + 8 for b in boxes
        ) if boxes else 46
        box_h = max(46, box_h)
        layer_h = 12 * 2 + rows * box_h + (rows - 1) * GAP
        layout.append({"layer": layer, "cols": cols, "rows": rows,
                       "box_w": box_w, "box_h": box_h, "boxes": boxes, "h": layer_h})

    total_h = sum(item["h"] for item in layout) + LAYER_GAP * (len(layout) - 1)
    out = [svg_open(CANVAS_W, total_h + PAD * 2, v["title"])]
    top = PAD

    for idx, item in enumerate(layout):
        main, tint = PALETTE[idx % len(PALETTE)]
        layer = item["layer"]
        ly = top
        # 层外框 + 左侧竖标签
        out.append(rect(x0, ly, main_x1 - x0, item["h"], "none", BORDER, 1.0))
        out.append(rect(x0, ly, TAB_W, item["h"], tint))
        out.append(rect(x0, ly, 3, item["h"], main))
        name_lines = wrap(layer["name"], 12, TAB_W - 12, bold=True, max_lines=3)
        nh = LINE_H * len(name_lines)
        out.append('<g text-anchor="middle">' + "".join(
            '<text x="%.1f" y="%.1f" font-size="12" fill="%s" font-weight="700">%s</text>'
            % (x0 + TAB_W / 2 + 2, ly + (item["h"] - nh) / 2 + 12 + k * LINE_H, INK, escape(s))
            for k, s in enumerate(name_lines)) + "</g>")
        # 模块网格
        for k, b in enumerate(item["boxes"]):
            r, c = divmod(k, item["cols"])
            bx = area_x0 + c * (item["box_w"] + GAP)
            by = ly + 12 + r * (item["box_h"] + GAP)
            out.append(rect(bx, by, item["box_w"], item["box_h"], "#FFFFFF", BORDER, 1.0))
            out.append(rect(bx, by, item["box_w"], 3, main))
            content_h = LINE_H * (len(b["name_lines"]) + len(b["sub"]))
            base = by + (item["box_h"] - content_h) / 2 + 13
            out.append('<g text-anchor="middle">' + "".join(
                '<text x="%.1f" y="%.1f" font-size="13" fill="%s" font-weight="700">%s</text>'
                % (bx + item["box_w"] / 2, base + i * LINE_H, INK, escape(s))
                for i, s in enumerate(b["name_lines"])) + "".join(
                '<text x="%.1f" y="%.1f" font-size="11" fill="%s">%s</text>'
                % (bx + item["box_w"] / 2, base + (len(b["name_lines"]) + i) * LINE_H, MUTED,
                   ellipsize(s, 11, item["box_w"] - 10))
                for i, s in enumerate(b["sub"])) + "</g>")
        # 层间箭头
        if idx < len(layout) - 1:
            gy0 = ly + item["h"]
            for frac in (0.25, 0.5, 0.75):
                out.append(arrow_v(area_x0 + area_w * frac, gy0 + 2, gy0 + LAYER_GAP - 2, MUTED))
        top += item["h"] + LAYER_GAP

    # 侧栏（安全/运维体系等）
    for i, s in enumerate(side):
        main, tint = PALETTE[(i + 3) % len(PALETTE)]
        sx = main_x1 + SIDE_GAP + i * (SIDE_W + SIDE_GAP)
        out.append(rect(sx, PAD, SIDE_W, total_h, tint, BORDER, 1.0))
        out.append(rect(sx, PAD, SIDE_W, 3, main))
        name_lines = wrap(s["name"], 12, SIDE_W - 10, bold=True, max_lines=3)
        block_h = LINE_H * len(name_lines) + 10 + 19 * len(s["items"])
        top0 = PAD + max(10.0, (total_h - block_h) / 2)
        out.append('<g text-anchor="middle">' + "".join(
            '<text x="%.1f" y="%.1f" font-size="12" fill="%s" font-weight="700">%s</text>'
            % (sx + SIDE_W / 2, top0 + 13 + k * LINE_H, INK, escape(s2))
            for k, s2 in enumerate(name_lines)) + "</g>")
        out.append('<g text-anchor="middle">' + "".join(
            '<text x="%.1f" y="%.1f" font-size="11" fill="%s">%s</text>'
            % (sx + SIDE_W / 2, top0 + 13 + len(name_lines) * LINE_H + 10 + k * 19, MUTED,
               escape(ellipsize(it, 11, SIDE_W - 8)))
            for k, it in enumerate(s["items"])) + "</g>")

    out.append("</svg>")
    return "".join(out)
