# -*- coding: utf-8 -*-
"""bidfig — 标书生成图表原语库（LLM 出数据，代码定版式）。

背景与设计见 docs/标书生成-图表原语库-原型-20261018.md。公开接口：

- render_spec(spec)                 按 kind 渲染 SVG（校验失败抛 BidfigSpecError）
- render_bidfig_fences(md)          API 出口：```bidfig JSON → ```bidfig-svg
- close_unterminated_bidfig_fence / clamp_bidfig_blocks   agent 落盘前修补
"""
from .fences import (BIDFIG_LANG, BIDFIG_SVG_LANG, INVALID_PLACEHOLDER,
                     bidfig_fence_payloads, clamp_bidfig_blocks,
                     close_unterminated_bidfig_fence, extract_bidfig_fences,
                     render_bidfig_fences, render_spec, validate_bidfig_payload)
from .gantt import BidfigSpecError

__all__ = [
    "BIDFIG_LANG", "BIDFIG_SVG_LANG", "INVALID_PLACEHOLDER",
    "BidfigSpecError",
    "bidfig_fence_payloads", "clamp_bidfig_blocks", "close_unterminated_bidfig_fence",
    "extract_bidfig_fences", "render_bidfig_fences", "render_spec", "validate_bidfig_payload",
]
