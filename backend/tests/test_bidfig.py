# -*- coding: utf-8 -*-
"""bidfig 图表原语库单测：渲染良构性/确定性、校验、围栏修补与出口转换。

对应原型验收标准：代码产出的 SVG 必须是良构 XML（mermaid 真机曾 11/19 块
非良构静默降级）、同数据逐字节可复现、坏一块不阻断整节。
"""
import json
import xml.etree.ElementTree as ET

import pytest

from backend.agent.bidfig import (
    INVALID_PLACEHOLDER,
    BidfigSpecError,
    clamp_bidfig_blocks,
    close_unterminated_bidfig_fence,
    render_bidfig_fences,
    render_spec,
)
from backend.agent.bidfig.gantt import _ticks
from backend.agent.bidfig.primitives import ellipsize, text_w, wrap
import datetime as dt

GANTT_SPEC = {
    "kind": "gantt",
    "title": "实施进度计划",
    "phases": [{"name": "部署"}, {"name": "开发"}],
    "tasks": [
        {"name": "标品部署", "phase": 0, "start": "2026-11-02", "end": "2026-12-10"},
        {"name": "定制开发与测试", "phase": 1, "start": "2026-12-11", "end": "2027-03-31"},
    ],
    "milestones": [{"name": "M1 上线", "date": "2026-12-10"}],
}

ARCH_SPEC = {
    "kind": "arch",
    "title": "系统总体架构",
    "layers": [
        {"name": "展现层", "modules": [{"name": "编制客户端", "sub": ["Word 插件"]}]},
        {"name": "服务层", "modules": [{"name": "生成服务"}, {"name": "审查服务"}]},
    ],
    "side": [{"name": "安全体系", "items": ["传输加密"]}],
}


def _parse(svg: str) -> ET.Element:
    assert svg.startswith("<svg")
    return ET.fromstring(svg)


def test_gantt_render_wellformed_and_deterministic():
    svg = render_spec(GANTT_SPEC)
    _parse(svg)
    assert render_spec(GANTT_SPEC) == svg
    assert "<title>实施进度计划</title>" in svg


def test_gantt_optional_range_and_phase_by_name():
    spec = {
        "kind": "gantt", "title": "单任务",
        "phases": [{"name": "唯一阶段"}],
        "tasks": [{"name": "任务A", "phase": "唯一阶段",
                   "start": "2026-11-01", "end": "2026-11-01"}],
    }
    root = _parse(render_spec(spec))  # 同日起止自动扩为 1 天


def test_gantt_validation_errors_aggregated():
    bad = {"kind": "gantt", "title": "", "tasks": [{"name": "", "start": "x", "end": "2026-01-02"}]}
    with pytest.raises(BidfigSpecError) as ei:
        render_spec(bad)
    msg = str(ei.value)
    assert "title" in msg and "日期" in msg
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "gantt", "title": "空", "tasks": [], "milestones": []})
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "gantt", "title": "序反",
                     "tasks": [{"name": "A", "start": "2026-02-01", "end": "2026-01-01"}]})
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "gantt", "title": "越界",
                     "phases": [{"name": "P"}],
                     "tasks": [{"name": "A", "phase": 3,
                                "start": "2026-01-01", "end": "2026-01-02"}]})


def test_unknown_kind_rejected():
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "pie", "title": "x"})


def test_gantt_tick_interval_adaptive():
    d0 = dt.date(2026, 1, 5)
    assert len(_ticks(d0, d0 + dt.timedelta(days=5))) == 6          # ≤11 天按日
    weekly = _ticks(d0, d0 + dt.timedelta(days=30))
    assert 3 <= len(weekly) <= 6 and all(lbl for _, lbl in weekly)  # 按周
    months = _ticks(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert len(months) == 12                                          # 按月
    quarters = _ticks(dt.date(2024, 1, 1), dt.date(2027, 6, 30))
    assert all(lbl.endswith(("Q1", "Q2", "Q3", "Q4")) for _, lbl in quarters)  # >26 月按季


def test_arch_render_wellformed_and_deterministic():
    svg = render_spec(ARCH_SPEC)
    _parse(svg)
    assert render_spec(ARCH_SPEC) == svg
    assert "编制客户端" in svg and "安全体系" in svg


def test_arch_validation():
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "arch", "title": "空层", "layers": []})
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "arch", "title": "无模块",
                     "layers": [{"name": "L", "modules": []}]})
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "arch", "title": "侧栏超限",
                     "layers": [{"name": "L", "modules": [{"name": "M"}]}],
                     "side": [{"name": "S%d" % i, "items": ["a"]} for i in range(4)]})


def test_wrap_and_ellipsize_cjk():
    assert text_w("标书", 12) == 24
    assert text_w("ab", 12) == pytest.approx(12 * 0.62 * 2)
    lines = wrap("投标文件编制服务端", 13, 40)  # 每行 ≈3 个汉字
    assert all(text_w(ln, 13) <= 40 + 13 for ln in lines)
    assert "".join(lines) == "投标文件编制服务端"
    cut = ellipsize("项目实施进度计划横道图", 13, 60)
    assert cut.endswith("…") and text_w(cut, 13) <= 60 + 13


def test_render_fences_replaces_json_with_svg():
    md = (
        "进度安排如下：\n\n"
        "```bidfig\n" + json.dumps(GANTT_SPEC, ensure_ascii=False) + "\n```\n\n"
        "如上所示。\n"
    )
    out = render_bidfig_fences(md)
    assert "```bidfig\n" not in out
    assert "```bidfig-svg" in out
    assert "进度安排如下" in out and "如上所示。" in out
    svg = out.split("```bidfig-svg\n", 1)[1].split("\n```", 1)[0]
    _parse(svg)


def test_render_fences_degrades_gracefully():
    out = render_bidfig_fences("前文\n\n```bidfig\n{不是JSON\n```\n后文")
    assert INVALID_PLACEHOLDER in out and "后文" in out
    out2 = render_bidfig_fences('```bidfig\n{"kind":"pie","title":"x"}\n```')
    assert INVALID_PLACEHOLDER in out2
    # 合法 JSON 但校验失败也降级不抛异常
    out3 = render_bidfig_fences('```bidfig\n{"kind":"gantt","title":"t","tasks":[]}\n```')
    assert INVALID_PLACEHOLDER in out3


def test_render_fences_keeps_mermaid_untouched():
    md = "```mermaid\nflowchart TD\nA-->B\n```\n"
    assert render_bidfig_fences(md) == md


def test_close_unterminated_before_heading():
    body = "```bidfig\n{\"kind\":\"arch\"\n# 下一个标题\n正文"
    fixed = close_unterminated_bidfig_fence(body)
    lines = fixed.split("\n")
    assert lines[2] == "```" and lines[3] == "# 下一个标题"
    closed = "```bidfig\n{}\n```\n正文"
    assert close_unterminated_bidfig_fence(closed) == closed


def test_clamp_bidfig_blocks_limit():
    fence = lambda i: "```bidfig\n{\"kind\":\"arch\",\"title\":\"t%d\"}\n```" % i
    body = "\n".join(fence(i) for i in range(3))
    clamped = clamp_bidfig_blocks(body, limit=2)
    assert clamped.count("```bidfig\n") == 2
    assert INVALID_PLACEHOLDER in clamped and "t0" in clamped and "t1" in clamped


def test_agent_prompt_fig_mode_default_off():
    from backend.agent import bid_draft_agent as agent
    assert "```bidfig" not in agent.SECTION_SYSTEM_PROMPT
    assert "```mermaid" in agent.SECTION_SYSTEM_PROMPT
    assert "```bidfig" in agent.SECTION_SYSTEM_PROMPT_BIDFIG
    assert '"kind":"gantt"' in agent.SECTION_SYSTEM_PROMPT_BIDFIG
    assert '"kind":"arch"' in agent.SECTION_SYSTEM_PROMPT_BIDFIG
