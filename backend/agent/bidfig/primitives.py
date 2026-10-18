# -*- coding: utf-8 -*-
"""bidfig primitives — 标书图表原语库的共享绘图基件（纯 Python 拼 SVG 文本，零依赖）。

移植自 2026-10 河南应标项目的 hand-src/figlib.py 方法论：版式（折行、间距、
避让、配色）由确定性代码负责，LLM 只提供数据；几何约束修在原语里，一处
修复全体图表受益。

设计约定：
- 画布宽 CANVAS_W=1100（与前端 PNG_MAX_WIDTH_PX=2200 / scale=2 对齐），高度按内容计算；
- 字体栈与前端 chartAssets.ts 一致（光栅化发生在用户机 WebView2，中文字体天然可用）；
- 文本宽度用 CJK/ASCII 估宽（全角=字号、半角≈0.62 字号），所有换行先于渲染发生，
  杜绝"文字溢出容器"这类 mermaid 黑盒问题；
- 语义色板 PALETTE 主色+浅底成对使用，循环取色保证多图层/多任务条颜色稳定。
"""
from __future__ import annotations

import contextlib
import re as _re
import xml.etree.ElementTree as _ET
from xml.sax.saxutils import escape

FONT = "'Microsoft YaHei', 'PingFang SC', Arial, sans-serif"  # 内层单引号：作为双引号 XML 属性值嵌入
CANVAS_W = 1100
LINE_H = 15  # 13px 字号的行高（arch/flow/seq 共用）

INK = "#1F2937"
MUTED = "#6B7280"
LINE = "#E5E7EB"
BORDER = "#D1D5DB"
BAND = "#F3F4F6"

# (主色, 浅底) 循环色板
PALETTE: list[tuple[str, str]] = [
    ("#2563EB", "#DBEAFE"),
    ("#0F766E", "#CCFBF1"),
    ("#B45309", "#FEF3C7"),
    ("#7C3AED", "#EDE9FE"),
    ("#BE185D", "#FCE7F3"),
    ("#0369A1", "#E0F2FE"),
    ("#4D7C0F", "#ECFCCB"),
]

_CJK_RANGES = (
    (0x2E80, 0x9FFF), (0xAC00, 0xD7AF), (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF), (0x2000, 0x206F),
)

# P2 渲染自检：折行/截断的目标宽度整体乘上收紧系数（1.0=不收紧）。自检发现
# 文字或图形越出画布时，render_spec 会在 tightened(0.92) 里重渲一遍——同一份
# 数据重渲是确定性的，但文本占宽更保守，等效"自动重试一次版式修复"。
_TIGHTEN = 1.0


@contextlib.contextmanager
def tightened(factor: float):
    global _TIGHTEN
    prev = _TIGHTEN
    _TIGHTEN = factor
    try:
        yield
    finally:
        _TIGHTEN = prev


def _char_w(ch: str, size: float, bold: bool) -> float:
    if any(lo <= ord(ch) <= hi for lo, hi in _CJK_RANGES):
        return size
    return size * (0.66 if bold else 0.62)


def text_w(s: str, size: float, bold: bool = False) -> float:
    return sum(_char_w(c, size, bold) for c in s)


def wrap(s: str, size: float, maxw: float, bold: bool = False, max_lines: int = 0) -> list[str]:
    """CJK 感知贪心折行：先按已有换行拆段，再逐字累积断行。

    max_lines>0 时超出部分截断并末行补省略号。
    """
    maxw = maxw * _TIGHTEN
    out: list[str] = []
    for para in (s or "").split("\n"):
        line, w = "", 0.0
        for ch in para:
            cw = _char_w(ch, size, bold)
            if line and w + cw > maxw:
                out.append(line)
                line, w = ch, cw
            else:
                line += ch
                w += cw
        out.append(line)
    if max_lines and len(out) > max_lines:
        out = out[:max_lines]
        last = out[-1]
        while last and text_w(last + "…", size, bold) > maxw:
            last = last[:-1]
        out[-1] = last + "…"
    return out


def ellipsize(s: str, size: float, maxw: float, bold: bool = False) -> str:
    """单行截断到 maxw 宽（含省略号）。"""
    maxw = maxw * _TIGHTEN
    if text_w(s, size, bold) <= maxw:
        return s
    out = ""
    for ch in s:
        if text_w(out + ch + "…", size, bold) > maxw:
            return out + "…"
        out += ch
    return out


def tspans(x: float, y: float, lines: list[str], size: float, fill: str,
           weight: int = 400, lh: float | None = None, anchor: str = "start") -> str:
    lh = lh or size * 1.32
    parts = []
    for i, ln in enumerate(lines):
        parts.append(
            '<text x="%.1f" y="%.1f" font-size="%.1f" fill="%s" font-weight="%d" '
            'text-anchor="%s" font-family="%s">%s</text>'
            % (x, y + i * lh, size, fill, weight, anchor, FONT, escape(ln))
        )
    return "".join(parts)


def svg_open(w: float, h: float, title: str = "") -> str:
    head = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
        'viewBox="0 0 %.0f %.0f" font-family="%s">' % (w, h, w, h, FONT)
    )
    if title:
        head += "<title>%s</title>" % escape(title[:80])
    return head


def rect(x: float, y: float, w: float, h: float, fill: str, stroke: str | None = None,
         sw: float = 1.0, rx: float = 0.0) -> str:
    s = '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"' % (x, y, w, h, fill)
    if rx:
        s += ' rx="%.1f"' % rx
    if stroke:
        s += ' stroke="%s" stroke-width="%.1f"' % (stroke, sw)
    return s + "/>"


def line(x1: float, y1: float, x2: float, y2: float, stroke: str, sw: float = 1.0,
         dash: str | None = None) -> str:
    s = '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%.1f"' % (
        x1, y1, x2, y2, stroke, sw)
    if dash:
        s += ' stroke-dasharray="%s"' % dash
    return s + "/>"


def arrow_v(x: float, y1: float, y2: float, stroke: str) -> str:
    """竖直向下小箭头（层间流向）。"""
    head = 4.5
    return (
        line(x, y1, x, y2 - head, stroke, 1.6)
        + '<path d="M %.1f,%.1f L %.1f,%.1f L %.1f,%.1f Z" fill="%s"/>'
        % (x - head, y2 - head * 1.6, x, y2, x + head, y2 - head * 1.6, stroke)
    )


def diamond(cx: float, cy: float, r: float, fill: str, stroke: str) -> str:
    return (
        '<path d="M %.1f,%.1f L %.1f,%.1f L %.1f,%.1f L %.1f,%.1f Z" '
        'fill="%s" stroke="%s" stroke-width="1.2"/>'
        % (cx, cy - r, cx + r, cy, cx, cy + r, cx - r, cy, fill, stroke)
    )


def arrow_h(x1: float, x2: float, y: float, stroke: str, dash: str | None = None) -> str:
    """水平箭头（时序图消息；自动按 x1/x2 相对方向放箭头尖）。"""
    head = 4.5
    sign = 1.0 if x2 >= x1 else -1.0
    return (
        line(x1, y, x2 - sign * head, y, stroke, 1.6, dash)
        + '<path d="M %.1f,%.1f L %.1f,%.1f L %.1f,%.1f Z" fill="%s"/>'
        % (x2 - sign * head * 1.6, y - head, x2, y, x2 - sign * head * 1.6, y + head, stroke)
    )


_NUM_RE = _re.compile(r"^[-+]?\d+(?:\.\d+)?$")


def svg_bounds_issues(svg: str, safety: float = 1.08) -> list[str]:
    """P2 渲染后几何自检：扫描产出 SVG 内全部 text/rect/line 是否越出画布。

    - 文字宽度按 text_w 估算再乘 safety（真实字体（尤其中英文混排的 ASCII 宽字符）
      可能比估宽略宽，留 8% 余量），text-anchor 从祖先 <g> 继承；
    - rect/line 检查坐标是否在画布内（±0.5 容差）；
    - 纯估算零依赖：这是出稿前的最后一道机检（抓版式回归），不是像素级渲染验证。
    """
    root = _ET.fromstring(svg)

    def _num(value, default=0.0):
        return float(value) if value is not None and _NUM_RE.match(value.strip()) else default

    w = _num(root.get("width"), CANVAS_W)
    h = _num(root.get("height"), 0.0)
    issues: list[str] = []

    def walk(el, anchor: str, size: float, weight: int):
        anchor = el.get("text-anchor", anchor)
        size = _num(el.get("font-size"), size)
        weight = int(_num(el.get("font-weight"), weight))
        tag = el.tag.split("}")[-1]
        if tag == "text":
            txt = "".join(el.itertext())
            if txt.strip():
                x = _num(el.get("x"))
                tw = text_w(txt, size, weight >= 600) * safety
                lo, hi = (x - tw / 2, x + tw / 2) if anchor == "middle" else (
                    (x - tw, x) if anchor == "end" else (x, x + tw))
                if lo < -1.0 or hi > w + 1.0:
                    issues.append("text 越界: %r x=%.0f 宽≈%.0f (画布 %.0f)" % (txt[:12], x, tw, w))
        elif tag == "rect":
            x, y = _num(el.get("x")), _num(el.get("y"))
            rw, rh = _num(el.get("width")), _num(el.get("height"))
            if x < -0.5 or y < -0.5 or x + rw > w + 0.5 or y + rh > h + 0.5:
                issues.append("rect 越界: x=%.0f y=%.0f w=%.0f h=%.0f (画布 %.0fx%.0f)" % (x, y, rw, rh, w, h))
        elif tag == "line":
            xa, ya, xb, yb = (_num(el.get(k)) for k in ("x1", "y1", "x2", "y2"))
            if min(xa, xb) < -0.5 or max(xa, xb) > w + 0.5 or min(ya, yb) < -0.5 or max(ya, yb) > h + 0.5:
                issues.append("line 越界: (%.0f,%.0f)-(%.0f,%.0f) (画布 %.0fx%.0f)" % (xa, ya, xb, yb, w, h))
        for child in el:
            walk(child, anchor, size, weight)

    walk(root, "start", 12.0, 400)
    return issues
