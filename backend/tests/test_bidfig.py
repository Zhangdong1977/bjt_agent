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

FLOW_SPEC = {
    "kind": "flow",
    "title": "项目实施与验收流程",
    "phases": [
        {"name": "准备阶段", "steps": [{"name": "需求调研", "note": "对接各业务部门"}]},
        {"name": "实施阶段", "steps": [{"name": "平台部署"}, {"name": "定制开发"}]},
        {"name": "验收阶段", "steps": [{"name": "初验与终验", "note": "按招标文件验收口径"}]},
    ],
}

SEQ_SPEC = {
    "kind": "seq",
    "title": "系统对接交互时序",
    "actors": ["投标人客户端", "评标平台", "运营后台"],
    "messages": [
        {"from": 0, "to": 1, "text": "上传投标文件"},
        {"from": 1, "to": 2, "text": "发起解析与合规校验"},
        {"from": 2, "to": 2, "text": "生成审查报告"},
        {"from": 2, "to": 0, "text": "返回审查结果", "dash": True},
    ],
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
    assert '"kind":"flow"' in agent.SECTION_SYSTEM_PROMPT_BIDFIG
    assert '"kind":"seq"' in agent.SECTION_SYSTEM_PROMPT_BIDFIG


def test_chart_quality_stats_summary_slice():
    from backend.agent.bid_draft_agent import chart_quality_stats
    md = (
        "# 进度\n\n正文（请补充）一处。\n\n"
        "```mermaid\nflowchart TD\nA-->B\n```\n\n"
        "```bidfig\n" + json.dumps(GANTT_SPEC, ensure_ascii=False) + "\n```\n\n"
        '```bidfig\n{"kind":"gantt","bad":1}\n```\n\n'
        + INVALID_PLACEHOLDER + "\n"
    )
    assert chart_quality_stats(md) == {
        "mermaid": 1, "bidfig": 2, "bidfig_valid": 1, "degraded": 1, "fact_gaps": 1,
    }


# ---------------------------------------------------------------- P4 扩类：flow / seq


def test_flow_render_wellformed_and_deterministic():
    svg = render_spec(FLOW_SPEC)
    _parse(svg)
    assert render_spec(FLOW_SPEC) == svg
    assert "<title>项目实施与验收流程</title>" in svg
    assert "需求调研" in svg and "验收阶段" in svg


def test_flow_single_column_without_phases():
    spec = {"kind": "flow", "title": "故障响应流程",
            "steps": [{"name": "受理报修", "note": "7×24 热线"}, {"name": "分级派单"}, {"name": "处理与回访"}]}
    svg = render_spec(spec)
    _parse(svg)
    assert "受理报修" in svg


def test_flow_escapes_markup_in_names():
    spec = {"kind": "flow", "title": "转义",
            "steps": [{"name": "A&B<测试>"}, {"name": "正常"}]}
    _parse(render_spec(spec))  # 含 & < > 的名称不得破坏 XML 良构性


def test_flow_validation():
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "flow", "title": "无步骤", "steps": []})
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "flow", "title": "超列",
                     "phases": [{"name": "P%d" % i, "steps": [{"name": "s"}]} for i in range(6)]})
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "flow", "title": "缺名",
                     "phases": [{"name": "A", "steps": [{"name": "s"}]}, {"steps": [{"name": "s"}]}]})


def test_seq_render_wellformed_and_deterministic():
    svg = render_spec(SEQ_SPEC)
    _parse(svg)
    assert render_spec(SEQ_SPEC) == svg
    assert "投标人客户端" in svg and "返回审查结果" in svg
    assert "1. 上传投标文件" in svg  # 消息自动编号


def test_seq_self_message_loop():
    spec = {"kind": "seq", "title": "自交互", "actors": ["A 系统", "B 系统"],
            "messages": [{"from": 0, "to": 0, "text": "本地校验"}, {"from": 0, "to": 1, "text": "提交"}]}
    _parse(render_spec(spec))


def test_seq_validation():
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "seq", "title": "单参与方", "actors": ["A"], "messages": [{"from": 0, "to": 0, "text": "x"}]})
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "seq", "title": "越界", "actors": ["A", "B"],
                     "messages": [{"from": 0, "to": 5, "text": "x"}]})
    with pytest.raises(BidfigSpecError):
        render_spec({"kind": "seq", "title": "空消息", "actors": ["A", "B"],
                     "messages": [{"from": 0, "to": 1, "text": ""}]})


def test_seq_index_accepts_numeric_string():
    spec = {"kind": "seq", "title": "字符串下标", "actors": ["A", "B"],
            "messages": [{"from": "0", "to": "1", "text": "调用"}]}
    _parse(render_spec(spec))


# ---------------------------------------------------------------- P2 渲染自检


def test_svg_bounds_issues_catches_overflow():
    from backend.agent.bidfig.primitives import svg_bounds_issues
    bad = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
           '<text x="150" y="50" font-size="12">很长的一段文字超出画布右边界了</text></svg>')
    assert svg_bounds_issues(bad)  # start 锚点 x=150 + 文字宽 > 200
    ok = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
          '<rect x="10" y="10" width="100" height="50" fill="none"/></svg>')
    assert not svg_bounds_issues(ok)


def test_all_primitives_pass_selfcheck():
    from backend.agent.bidfig.primitives import svg_bounds_issues
    for spec in (GANTT_SPEC, ARCH_SPEC, FLOW_SPEC, SEQ_SPEC):
        assert not svg_bounds_issues(render_spec(spec)), spec["kind"]


def test_render_spec_autotighten_retry(monkeypatch):
    """自检发现越界 → tightened(0.92) 重渲一次；修复则采用重渲稿。"""
    import backend.agent.bidfig.fences as fences
    import backend.agent.bidfig.primitives as primitives

    good = render_spec(FLOW_SPEC)
    calls = []

    def fake(spec):
        calls.append(primitives._TIGHTEN)
        if primitives._TIGHTEN >= 1.0:
            return good.replace("</svg>", (
                '<text x="1050" y="20" font-size="13">很长很长很长很长很长的占位文字串</text></svg>'))
        return good

    monkeypatch.setitem(fences._RENDERS, "flow", fake)
    result = fences.render_spec(FLOW_SPEC)
    assert calls == [1.0, 0.92]          # 默认一遍 + 收紧重渲一遍
    assert result == good                # 重渲稿通过自检被采用
    assert primitives._TIGHTEN == 1.0    # 上下文退出后恢复


def test_render_spec_selfcheck_fallback_keeps_original(monkeypatch):
    """收紧后仍越界 → 保留原稿不抛异常（图表坏不阻断）。"""
    import backend.agent.bidfig.fences as fences

    always_bad = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
                  '<text x="150" y="50" font-size="12">超宽文字超宽文字超宽文字超宽</text></svg>')
    monkeypatch.setitem(fences._RENDERS, "flow", lambda spec: always_bad)
    assert fences.render_spec({"kind": "flow", "title": "t"}) == always_bad


# ---------------------------------------------------------- 2026-10-24 补充：pie / bar

PIE_SPEC = {
    "kind": "pie", "title": "项目团队人员结构", "unit": "人",
    "items": [
        {"name": "高级工程师", "value": 12},
        {"name": "中级工程师", "value": 20},
        {"name": "项目经理", "value": 3},
        {"name": "实施工程师", "value": 8},
    ],
}

BAR_SPEC = {
    "kind": "bar", "title": "关键性能指标对比", "unit": "ms",
    "groups": [
        {"name": "页面响应时间", "bars": [
            {"name": "招标要求", "value": 500}, {"name": "我方应答", "value": 200}]},
        {"name": "并发处理能力", "bars": [
            {"name": "招标要求", "value": 1000}, {"name": "我方应答", "value": 1200}]},
        {"name": "数据处理吞吐（条/秒）", "bars": [
            {"name": "招标要求", "value": 800}, {"name": "我方应答", "value": 1500}]},
    ],
}


def test_pie_render_wellformed_deterministic_selfcheck():
    svg = render_spec(PIE_SPEC)
    _parse(svg)
    assert render_spec(PIE_SPEC) == svg
    assert "<title>项目团队人员结构</title>" in svg
    assert svg.count("<path") == len(PIE_SPEC["items"])   # 每项一个环形扇区
    assert "合计" in svg and "43 人" in svg               # unit 提供时中心显合计（12+20+3+8）
    assert "27.9%" in svg                                  # 12/43 百分比由渲染器算，不由 LLM 给
    from backend.agent.bidfig.primitives import svg_bounds_issues
    assert not svg_bounds_issues(svg)


def test_pie_no_unit_hides_center_and_small_slice_has_no_label():
    spec = {"kind": "pie", "title": "t",
            "items": [{"name": "a", "value": 100}, {"name": "b", "value": 1}]}
    svg = render_spec(spec)
    _parse(svg)
    assert "合计" not in svg                                # 无 unit 中心留白
    # 1% 扇区 <5% 不放扇区内标签：扇区内白色百分比文本只有 1 处（大扇区 99.0%）
    assert svg.count('fill="#FFFFFF" font-weight="600"') == 1 and "99.0%" in svg
    assert "1.0%" in svg                                   # 小项占比仍完整出现在图例行


def test_pie_validation_errors():
    for bad in (
        {"kind": "pie", "title": "", "items": [{"name": "a", "value": 1}, {"name": "b", "value": 1}]},
        {"kind": "pie", "title": "t", "items": [{"name": "a", "value": 1}]},
        {"kind": "pie", "title": "t", "items": [{"name": "a", "value": 0}, {"name": "b", "value": 0}]},
        {"kind": "pie", "title": "t", "items": [{"name": "a", "value": "x"}, {"name": "b", "value": 1}]},
        {"kind": "pie", "title": "t", "items": [{"name": "a", "value": -1}, {"name": "b", "value": 1}]},
    ):
        with pytest.raises(BidfigSpecError):
            render_spec(bad)


def test_bar_render_wellformed_deterministic_selfcheck():
    svg = render_spec(BAR_SPEC)
    _parse(svg)
    assert render_spec(BAR_SPEC) == svg
    assert "<title>关键性能指标对比</title>" in svg
    assert svg.count("单位：ms") == 1
    assert svg.count("招标要求") == 1 and svg.count("我方应答") == 1  # 图例按名去重
    assert "2000" in svg                                     # nice_max(1500)=2000 纵轴顶格刻度
    assert svg.count("1500") == 1                            # 柱顶数值标签（1500 不是五等分刻度）
    from backend.agent.bidfig.primitives import svg_bounds_issues
    assert not svg_bounds_issues(svg)


def test_bar_same_name_same_color_and_nice_axis():
    from backend.agent.bidfig import bar as bar_mod
    assert bar_mod._nice_max(1500) == 2000.0
    assert bar_mod._nice_max(999) == 1000.0
    assert bar_mod._nice_max(0.4) == 0.5
    assert bar_mod._nice_max(11) == 20.0
    spec = {"kind": "bar", "title": "t",
            "groups": [{"name": "g1", "bars": [{"name": "A", "value": 3}, {"name": "B", "value": 5}]},
                       {"name": "g2", "bars": [{"name": "A", "value": 4}, {"name": "B", "value": 1}]}]}
    svg = render_spec(spec)
    fills = [seg.split('fill="')[1].split('"')[0] for seg in svg.split('<rect ')[1:]]
    # rect 序：图例A/B 色块 → 每组 [柱A,柱A高亮,柱B,柱B高亮]；同名柱跨组同色、A≠B
    legend_a, legend_b = fills[0], fills[1]
    bar_a1, bar_b1, bar_a2, bar_b2 = fills[2], fills[4], fills[6], fills[8]
    assert legend_a == bar_a1 == bar_a2 and legend_b == bar_b1 == bar_b2
    assert bar_a1 != bar_b1


def test_bar_validation_errors():
    for bad in (
        {"kind": "bar", "title": "t", "groups": []},
        {"kind": "bar", "title": "t", "groups": [{"name": "g", "bars": []}]},
        {"kind": "bar", "title": "t", "groups": [{"name": "g", "bars": [{"name": "v", "value": 0}]}]},
        {"kind": "bar", "title": "t", "groups": [{"name": "", "bars": [{"name": "v", "value": 1}]}]},
        {"kind": "bar", "title": "t", "groups": [{"name": "g", "bars": [{"name": "v", "value": "abc"}]}]},
    ):
        with pytest.raises(BidfigSpecError):
            render_spec(bad)


def test_pie_bar_registered_in_renders_and_prompt():
    import backend.agent.bidfig.fences as fences
    assert set(fences._RENDERS) >= {"gantt", "arch", "flow", "seq", "pie", "bar"}
    from backend.agent.bid_draft_agent import SECTION_SYSTEM_PROMPT_BIDFIG
    assert '"kind":"pie"' in SECTION_SYSTEM_PROMPT_BIDFIG
    assert '"kind":"bar"' in SECTION_SYSTEM_PROMPT_BIDFIG
    assert "占比构成" not in SECTION_SYSTEM_PROMPT_BIDFIG.split("⑨")[0].split("⑥")[0]  # 占比已移出降级清单
