# -*- coding: utf-8 -*-
"""bidfig fences — ```bidfig JSON 围栏的解析、修补与出口渲染。

职责分层（与 agent 的 mermaid 工具同构，便于对称维护）：
- agent 落盘前：close_unterminated_bidfig_fence / clamp_bidfig_blocks 修补 LLM 输出；
- API 出口处：render_bidfig_fences 把 JSON 围栏确定性渲染为 ```bidfig-svg 围栏，
  前端 chartAssets 按"已渲染 SVG"光栅化走既有 bjt-chart:// 通道入 Word。
  磁盘上的 section md 始终保存 JSON 源（可 diff、可数据级重渲），产物即时生成。
"""
from __future__ import annotations

import json
import logging

from . import arch, gantt
from .gantt import BidfigSpecError

logger = logging.getLogger(__name__)

BIDFIG_LANG = "bidfig"
BIDFIG_SVG_LANG = "bidfig-svg"
INVALID_PLACEHOLDER = "（图表数据无效，已省略）"
_RENDERS = {"gantt": gantt.render, "arch": arch.render}


def render_spec(spec: dict) -> str:
    """按 spec['kind'] 分发渲染；数据不合法抛 BidfigSpecError。"""
    if not isinstance(spec, dict):
        raise BidfigSpecError("图表数据必须是 JSON 对象")
    kind = str(spec.get("kind") or "").strip()
    fn = _RENDERS.get(kind)
    if fn is None:
        raise BidfigSpecError("不支持的图表类型 kind=%r（支持 gantt/arch）" % kind)
    return fn(spec)


def _iter_open_lines(lines: list[str]):
    """产出 (open_idx, close_idx or None)：```bidfig 开栏与其闭栏行号。"""
    i = 0
    while i < len(lines):
        if lines[i].strip().lower().startswith("```" + BIDFIG_LANG):
            close = i + 1
            while close < len(lines) and not lines[close].strip().startswith("```"):
                close += 1
            yield i, (close if close < len(lines) else None)
            i = close if close < len(lines) else len(lines)
            i += 1
        else:
            i += 1


def extract_bidfig_fences(text: str) -> list[dict]:
    """定位全部已闭合的 ```bidfig 围栏，返回行号区间 {start, end}（含首尾栏）。"""
    lines = (text or "").split("\n")
    return [{"start": s, "end": c} for s, c in _iter_open_lines(lines) if c is not None]


def close_unterminated_bidfig_fence(body: str) -> str:
    """漏写闭合围栏时，在其后第一个 markdown 标题行前补 ```（无标题则文末）。"""
    lines = (body or "").split("\n")
    fixed = lines[:]
    offset = 0
    for start, close in _iter_open_lines(lines):
        if close is None:
            insert = next(
                (k for k in range(start + 1, len(lines)) if lines[k].lstrip().startswith("#")),
                len(fixed),
            )
            fixed.insert(insert + offset, "```")
            offset += 1
    return "\n".join(fixed)


def clamp_bidfig_blocks(body: str, limit: int = 2) -> str:
    """每节 bidfig 图上限（与 mermaid clamp 同款占位文案）；倒序替换防行号位移。"""
    lines = (body or "").split("\n")
    fences = extract_bidfig_fences(body)
    for extra in reversed(fences[limit:]):
        lines[extra["start"]: extra["end"] + 1] = [INVALID_PLACEHOLDER]
    return "\n".join(lines)


def render_bidfig_fences(md: str) -> str:
    """API 出口转换：```bidfig JSON → ```bidfig-svg（渲染失败降级为占位说明）。

    非法 JSON / 校验失败 / 未知 kind 均不抛异常——图表坏一块不阻断整节正文。
    """
    lines = (md or "").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        matched = False
        if lines[i].strip().lower().startswith("```" + BIDFIG_LANG):
            close = i + 1
            while close < len(lines) and not lines[close].strip().startswith("```"):
                close += 1
            if close < len(lines):
                payload = "\n".join(lines[i + 1:close]).strip()
                svg, err = None, None
                try:
                    spec = json.loads(payload)
                    svg = render_spec(spec)
                except BidfigSpecError as exc:
                    err = str(exc)
                except (ValueError, TypeError) as exc:
                    err = "JSON 解析失败: %s" % exc
                if svg:
                    out.extend(["```" + BIDFIG_SVG_LANG, svg, "```"])
                else:
                    logger.warning("bidfig render degraded: %s", err or "unknown")
                    out.append(INVALID_PLACEHOLDER)
                i = close + 1
                matched = True
        if not matched:
            out.append(lines[i])
            i += 1
    return "\n".join(out)
