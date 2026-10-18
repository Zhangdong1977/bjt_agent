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

from xml.sax.saxutils import escape

FONT = "'Microsoft YaHei', 'PingFang SC', Arial, sans-serif"  # 内层单引号：作为双引号 XML 属性值嵌入
CANVAS_W = 1100

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
