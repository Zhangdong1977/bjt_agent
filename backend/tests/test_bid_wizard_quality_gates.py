# -*- coding: utf-8 -*-
"""质检门禁扩展（2026-10-25 真实成稿复盘轮）单测：

覆盖四组修复——
① 图表编号：caption_duplication 检出 + renumber_captions 确定性重排（含重号消歧）；
② 规划图兑现：planned_chart_missing（叶子 error）/ planned_chart_on_chapter（父章 warning）；
③ 禁用词：技术用语豁免（唯一键/绝对时间/第一部分不报警）+ 残留升级 error 进 auto_fix；
④ 评分覆盖：uncovered/gate_blocking（证书类豁免）+ generate_spec 覆盖重试；
⑤ auto_fix 扩展：确定性重编号落盘 + 超稿压缩 finding（env 阈值可关）。
"""
from __future__ import annotations

import pytest

import backend.agent.bid_wizard_agent as wiz
from backend.agent.bid_doc_checks import (
    _forbidden_residual_count,
    caption_duplication_issues,
    draft_checks,
    planned_chart_issues,
    renumber_captions,
)


def _section(node_id, content, **extra):
    base = {"node_id": node_id, "title": node_id, "content": content}
    base.update(extra)
    return base


# ---------------------------------------------------------------- ① 图表编号


def test_caption_duplication_detected_on_later_node():
    sections = [
        _section("7.1", "## 7.1\n\n表7-1 项目组织与职责表\n\n|a|\n|-|\n|1|"),
        _section("7.4", "## 7.4\n\n表7-1 测试类型与通过标准\n\n|b|\n|-|\n|2|"),
    ]
    issues = caption_duplication_issues(sections)
    assert len(issues) == 1
    assert issues[0]["node_id"] == "7.4"
    assert issues[0]["severity"] == "error"
    assert issues[0]["type"] == "caption_duplication"


def test_caption_duplication_ignores_inline_references():
    sections = [
        _section("2.1", "## 2.1\n\n表2-1 参数表\n\n|a|\n|-|\n|1|"),
        _section("2.2", "## 2.2\n\n参数定义见表2-1，本章不再单列。"),
    ]
    assert caption_duplication_issues(sections) == []


def test_renumber_fixes_duplicates_and_updates_refs():
    sections = [
        _section("5.1", "接口清单见表5-1。\n\n表5-1 接口清单\n\n|a|\n|-|\n|1|"),
        _section("5.2", "见表5-1 的兑换要素。\n\n表5-1 单点登录要素\n\n|b|\n|-|\n|2|"),
    ]
    new_sections, stats = renumber_captions(sections)
    assert stats["remapped"] == 1  # 只有重号侧题注变了号（引用按首处口径不动）
    assert caption_duplication_issues(new_sections) == []
    by_id = {s["node_id"]: s["content"] for s in new_sections}
    # 5.1 首处题注保持 5-1；5.2 重号题注顺延为 5-2，引用跟随首处口径保持 5-1
    assert "表5-1 接口清单" in by_id["5.1"]
    assert "表5-2 单点登录要素" in by_id["5.2"]
    assert "见表5-1 的兑换要素" in by_id["5.2"]
    assert "见表5-1。" in by_id["5.1"]


def test_renumber_bidfig_json_title_and_fullwidth_dash():
    sections = [
        _section("3.1", '正文。\n\n```bidfig\n{"kind":"flow","title":"图3－1 旧流程","phases":[]}\n```\n'),
        _section("3.2", '正文。\n\n```bidfig\n{"kind":"flow","title":"图3-1 新流程","phases":[]}\n```\n'),
    ]
    assert caption_duplication_issues(sections), "JSON title 内编号应识别为题注"
    new_sections, stats = renumber_captions(sections)
    assert caption_duplication_issues(new_sections) == []
    joined = "\n".join(s["content"] for s in new_sections)
    assert "图3-1 旧流程" in joined and "图3-2 新流程" in joined


def test_renumber_noop_when_clean_sequential():
    sections = [
        _section("1.1", "表1-1 甲\n\n表1-2 乙"),
        _section("1.2", "表1-3 丙"),
    ]
    new_sections, stats = renumber_captions(sections)
    assert stats["changed_nodes"] == []
    assert new_sections[0]["content"] == sections[0]["content"]


def test_normalize_figure_captions_strips_renumbers_and_removes_stray():
    """图题唯一权威（2026-10-25 复盘）：title 剥自带号+按章内顺序补号，围栏邻位冗余题注行删除。"""
    from backend.agent.bid_doc_checks import normalize_figure_captions

    sections = [
        _section("2.1", '```bidfig\n{"kind":"flow","title":"图2-1 投标业务流程","phases":[]}\n```\n\n正文见图2-1。'),
        _section("2.2", '```bidfig\n{"kind":"arch","title":"系统总体架构","layers":[]}\n```\n图2-9 冗余题注\n正文。'),
    ]
    out, stats = normalize_figure_captions(sections)
    assert '"title":"图2-1 投标业务流程"' in out[0]["content"]  # 剥旧号后按章内顺序补回同号（净不变）
    assert '"title":"图2-2 系统总体架构"' in out[1]["content"]  # 无号 title 补占位号
    assert "图2-9 冗余题注" not in out[1]["content"]  # 围栏邻位冗余题注行删除
    assert "正文见图2-1。" in out[0]["content"]  # 正文引用不动（交 renumber 收口）
    assert stats["stripped"] == 1
    assert stats["removed_lines"] == 1
    assert stats["changed_nodes"] == ["2.2"]


def test_normalize_then_renumber_restores_chapter_sequence():
    """真实复盘形态：修订章表号改乱（表1-5）+ 图题自带跨章旧号（图9-9）——
    归一+重排后全稿恢复章内顺序、零重号。"""
    from backend.agent.bid_doc_checks import normalize_figure_captions

    sections = [
        _section("1.1", "表1-1 甲\n\n|a|\n|-|\n|1|"),
        _section("1.2", '表1-5 乙\n\n|b|\n|-|\n|2|\n\n```bidfig\n{"kind":"pie","title":"图9-9 人员构成","items":[]}\n```'),
    ]
    out, _ = normalize_figure_captions(sections)
    out, stats = renumber_captions(out)
    joined = "\n".join(s["content"] for s in out)
    assert "表1-2 乙" in joined
    assert "图1-1 人员构成" in joined
    assert caption_duplication_issues(out) == []
    assert stats["remapped"] >= 1


# ---------------------------------------------------------------- ② 规划图兑现


def test_planned_chart_missing_on_leaf_is_error():
    sections = [_section("4.1", "## 4.1\n\n正文，无图。")]
    planned = {"4.1": [{"type": "mermaid", "title": "解析流程图"}]}
    issues = planned_chart_issues(sections, planned)
    assert [i["type"] for i in issues] == ["planned_chart_missing"]
    assert issues[0]["severity"] == "error"
    assert "解析流程图" in issues[0]["detail"]


def test_planned_chart_on_chapter_node_is_warning():
    sections = [_section("4", "## 4 导语"), _section("4.1", "## 4.1 正文")]
    planned = {"4": [{"type": "mermaid", "title": "总体流程图"}]}
    issues = planned_chart_issues(sections, planned)
    assert [i["type"] for i in issues] == ["planned_chart_on_chapter"]
    assert issues[0]["severity"] == "warning"


def test_planned_chart_satisfied_by_bidfig_fence():
    sections = [_section("4.1", "## 4.1\n\n```bidfig\n{\"kind\":\"flow\"}\n```\n")]
    planned = {"4.1": [{"type": "mermaid", "title": "流程图"}]}
    assert planned_chart_issues(sections, planned) == []


def test_draft_checks_error_priority_over_warning_cap():
    # 12 个 overlong warning + 1 个 caption error：error 必须在封顶结果里
    sections = [
        _section("2.1", "表2-9 甲\n\n|a|\n|-|\n|1|", word_count=9999, article_count=2, text_count=400),
        _section("2.2", "表2-9 乙\n\n|b|\n|-|\n|2|", word_count=9999, article_count=2, text_count=400),
    ]
    sections += [
        _section("3.%d" % i, "第 %d 章 正文内容填充。" % i, word_count=9999, article_count=2, text_count=400)
        for i in range(1, 11)
    ]
    checks = draft_checks(sections)
    types = [c["type"] for c in checks]
    assert "caption_duplication" in types
    assert types.count("overlong_section") <= 10


# ---------------------------------------------------------------- ③ 禁用词豁免


def test_forbidden_word_tech_exempt():
    joined = "主键为唯一键；每条记录归属唯一项目；绝对时间节点以招标文件为准；第一阶段完成部署。"
    assert _forbidden_residual_count(joined, "唯一") == (0, 2)
    assert _forbidden_residual_count(joined, "绝对") == (0, 1)
    assert _forbidden_residual_count(joined, "第一") == (0, 1)


def test_forbidden_word_residual_is_error_and_hinted():
    style_card = {"self_reference": "我方", "forbidden_words": ["唯一", "最先进"]}
    content = "我方方案是国内唯一具备该能力的方案。\n\n国内第一部分。"
    checks = draft_checks([_section("1.1", content)], style_card=style_card)
    fw = [c for c in checks if c["type"] == "forbidden_word"]
    assert fw and all(c["severity"] == "error" for c in fw)
    hint = wiz.AUTO_FIX_TYPE_HINTS.get("forbidden_word")
    assert hint and "技术术语" in hint


def test_self_praise_and_ai_flavor_markers():
    content = "本方案值得注意的一站式设计为采购人保驾护航。"
    checks = draft_checks([_section("1.1", content)])
    types = {c["type"] for c in checks}
    assert "ai_flavor" in types and "self_praise" in types


# ---------------------------------------------------------------- ④ 评分覆盖


def _scoring_items():
    return [
        {"title": "技术规范书应答", "weight": "12分", "criteria": "完全满足技术规范书得12分", "is_business": False},
        {"title": "管理体系认证证书", "weight": "6分", "criteria": "须提供证书扫描件", "is_business": False},
        {"title": "报价", "weight": "60分", "criteria": "评审价格", "is_business": True},
    ]


def _spec_covering_partial():
    return [
        {"node_id": "1", "title": "总体建设方案", "level": 1, "summary": "总体方案 响应得分点：总体建设方案"},
        {"node_id": "2", "title": "架构设计", "level": 2, "summary": "架构"},
    ]


def test_uncovered_scoring_items_excludes_business_and_covered():
    spec = [
        {"node_id": "1", "title": "总体建设方案", "level": 1, "summary": "响应得分点：总体建设方案"},
        {"node_id": "2", "title": "技术规范书逐条应答", "level": 1, "summary": "逐条应答 响应得分点：技术规范书应答"},
    ]
    uncovered = wiz.uncovered_scoring_items(spec, _scoring_items())
    assert [i["title"] for i in uncovered] == ["管理体系认证证书"]


def test_gate_blocking_excludes_certificate_like():
    blocking = wiz.gate_blocking_scoring_items(_spec_covering_partial(), {"scoring_items": _scoring_items()})
    # 技术规范书应答（未覆盖、非证书）阻断；证书类未覆盖但豁免
    assert [i["title"] for i in blocking] == ["技术规范书应答"]


@pytest.mark.asyncio
async def test_generate_spec_coverage_retry(monkeypatch):
    calls = {"n": 0}

    class _LLM:
        async def generate_json(self, system_prompt, user_prompt):
            calls["n"] += 1
            if calls["n"] == 1:
                return [
                    {"title": "总体建设方案", "level": 1, "summary": "响应得分点：总体建设方案", "article_count": 2, "text_count": 400},
                ]
            assert "技术规范书应答" in user_prompt, "重试 prompt 必须点名漏项"
            return [
                {"title": "总体建设方案", "level": 1, "summary": "响应得分点：总体建设方案", "article_count": 2, "text_count": 400},
                {"title": "技术规范书逐条应答", "level": 2, "summary": "逐条应答 响应得分点：技术规范书应答", "article_count": 2, "text_count": 400},
            ]

    analysis = {"scoring_items": [
        {"title": "技术规范书应答", "weight": "12分", "criteria": "★条款逐条应答"},
        {"title": "总体建设方案", "weight": "9分", "criteria": "内容完整"},
    ]}
    spec = await wiz.generate_spec(_LLM(), analysis=analysis, requirements_text="", material_index_text="")
    assert calls["n"] == 2
    assert not wiz.gate_blocking_scoring_items(spec, analysis)


# ---------------------------------------------------------------- ⑤ auto_fix 扩展


@pytest.fixture
def agent_factory(monkeypatch, tmp_path):
    import asyncio
    from backend.agent.bid_wizard_agent import BidWizardReviewAgent

    def make(revisions=None):
        agent = BidWizardReviewAgent(task_id="t-gate", workspace_dir=tmp_path, session_factory=None)
        monkeypatch.setattr(wiz, "WizardLLM", lambda **kwargs: object())

        async def fake_revise(self, node, **kwargs):
            new_content = (revisions or {}).get(node["node_id"])
            body = new_content if new_content is not None else kwargs["current_content"]
            import re as _re
            return {
                "node_id": node["node_id"],
                "title": node.get("title") or node["node_id"],
                "content": body,
                "word_count": len(_re.sub(r"\s", "", str(body))),
                "article_count": int(node.get("article_count") or 2),
                "text_count": int(node.get("text_count") or 400),
            }

        monkeypatch.setattr(BidWizardReviewAgent, "_revise_section", fake_revise)

        async def noop_set_status(node_id, **kwargs):
            return None

        monkeypatch.setattr(BidWizardReviewAgent, "_set_section_status", noop_set_status)
        return agent

    return make


@pytest.mark.asyncio
async def test_auto_fix_renumbers_and_persists(agent_factory, monkeypatch):
    persist = []
    agent = agent_factory()

    async def fake_persist(item, content):
        persist.append(str(item.get("node_id")))

    monkeypatch.setattr(agent, "_persist_section_content", fake_persist)
    spec = [
        {"node_id": "5.1", "title": "接口", "level": 2, "summary": "", "article_count": 2, "text_count": 200},
        {"node_id": "5.2", "title": "单点", "level": 2, "summary": "", "article_count": 2, "text_count": 200},
    ]
    sections = [
        _section("5.1", "## 5.1\n\n表5-1 接口清单\n\n|a|\n|-|\n|1|", word_count=50, article_count=2, text_count=200),
        _section("5.2", "## 5.2\n\n表5-1 单点登录\n\n|b|\n|-|\n|2|", word_count=50, article_count=2, text_count=200),
    ]
    final, checks, info = await agent.auto_fix_machine_errors(
        spec=spec, requirements={}, analysis={}, sections=sections
    )
    assert info["renumbered_nodes"] == ["5.2"]
    assert persist == ["5.2"]
    assert not [c for c in checks if c["type"] == "caption_duplication"]
    assert "表5-2 单点登录" in next(s["content"] for s in final if s["node_id"] == "5.2")


@pytest.mark.asyncio
async def test_auto_fix_compresses_overlong(agent_factory):
    long_body = "## 2.1\n\n" + "正文内容。" * 300  # ≈1500 字，规划 2×200=400 → >1.6x
    agent = agent_factory(revisions={"2.1": "## 2.1\n\n" + "精简后。" * 40})
    spec = [
        {"node_id": "2.1", "title": "架构", "level": 2, "summary": "", "article_count": 2, "text_count": 200},
    ]
    sections = [_section("2.1", long_body, word_count=len(long_body), article_count=2, text_count=200)]
    _, checks, info = await agent.auto_fix_machine_errors(
        spec=spec, requirements={}, analysis={}, sections=sections
    )
    assert "2.1" in info["compressed_nodes"]
    assert not [c for c in checks if c["type"] == "overlong_section"]


@pytest.mark.asyncio
async def test_auto_fix_overlong_env_off(agent_factory, monkeypatch):
    long_body = "## 2.1\n\n" + "正文内容。" * 300
    agent = agent_factory()
    monkeypatch.setenv("BID_WIZARD_AUTO_FIX_OVERLONG_RATIO", "0")
    spec = [{"node_id": "2.1", "title": "架构", "level": 2, "summary": "", "article_count": 2, "text_count": 200}]
    sections = [_section("2.1", long_body, word_count=len(long_body), article_count=2, text_count=200)]
    _, checks, info = await agent.auto_fix_machine_errors(
        spec=spec, requirements={}, analysis={}, sections=sections
    )
    assert info["compressed_nodes"] == []
    assert info["rounds"] == 0


@pytest.mark.asyncio
async def test_auto_fix_dispatches_forbidden_word_to_sections(tmp_path, monkeypatch):
    from backend.agent.bid_wizard_agent import BidWizardReviewAgent

    captured: dict[str, list] = {}

    async def fake_revise(self, node, **kwargs):
        captured[node["node_id"]] = kwargs["node_findings"]
        clean = "## %s\n\n我方以专属通道承载模型调用，主键为唯一键。" % node["node_id"]
        return {
            "node_id": node["node_id"],
            "title": node.get("title") or node["node_id"],
            "content": clean,
            "word_count": len(clean),
            "article_count": 2,
            "text_count": 400,
        }

    monkeypatch.setattr(BidWizardReviewAgent, "_revise_section", fake_revise)

    async def noop_status(node_id, **kwargs):
        return None

    monkeypatch.setattr(BidWizardReviewAgent, "_set_section_status", noop_status)
    agent = BidWizardReviewAgent(task_id="t-fw", workspace_dir=tmp_path, session_factory=None)
    spec = [
        {"node_id": "1.1", "title": "甲", "level": 2, "summary": "", "article_count": 2, "text_count": 400},
        {"node_id": "1.2", "title": "乙", "level": 2, "summary": "", "article_count": 2, "text_count": 400},
    ]
    sections = [
        _section("1.1", "## 1.1\n\n我方方案是国内唯一方案，数据层采用唯一键约束。", word_count=30, article_count=2, text_count=400),
        _section("1.2", "## 1.2\n\n正常内容，不含禁用词。", word_count=12, article_count=2, text_count=400),
    ]
    requirements = {"style_card": {"self_reference": "我方", "forbidden_words": ["唯一"]}}
    final, checks, info = await agent.auto_fix_machine_errors(
        spec=spec, requirements=requirements, analysis={}, sections=sections
    )
    assert "1.1" in captured and "1.2" not in captured
    assert any("唯一" in str(f.get("issue")) for f in captured["1.1"])
    assert not [c for c in checks if c["type"] == "forbidden_word"]
    assert info["remaining_errors"] == 0


async def _noop_coro():
    return None
