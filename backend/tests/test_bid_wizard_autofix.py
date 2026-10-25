"""质检自动修复闭环（2026-10-25 用户反馈）：auto_fix_machine_errors 的
发现映射 / 循环收敛 / 轮次上限 / 立场裁决下发 / 应急开关。"""

import pytest

from backend.agent import bid_wizard_agent as wiz


def _spec() -> list[dict]:
    return [
        {"node_id": "1", "title": "总体方案", "level": 1, "summary": "章首导语"},
        {"node_id": "1.1", "title": "架构设计", "level": 2, "summary": "架构"},
        {"node_id": "1.2", "title": "部署方案", "level": 2, "summary": "部署"},
    ]


def _section(node_id: str, title: str, content: str) -> dict:
    return {
        "node_id": node_id,
        "title": title,
        "content": content,
        "word_count": len(content.replace(" ", "")),
        "article_count": 2,
        "text_count": 400,
    }


@pytest.fixture()
def agent_factory(monkeypatch, tmp_path):
    """构造打桩 review agent：WizardLLM 打桩，_revise_section 换成可编程假实现。"""

    def _make(revise_impl):
        monkeypatch.setattr(wiz, "WizardLLM", lambda **kwargs: object())
        agent = wiz.BidWizardReviewAgent(
            task_id="task-autofix-test", workspace_dir=tmp_path, session_factory=None
        )
        calls: list[dict] = []

        async def fake_revise(node, *, current_content, node_findings, **kwargs):
            calls.append(
                {
                    "node_id": node["node_id"],
                    "current": current_content,
                    "findings": [dict(item) for item in node_findings],
                    "global": kwargs.get("global_findings_text") or "",
                }
            )
            result = revise_impl(node["node_id"], current_content, node_findings)
            return {
                "node_id": node["node_id"],
                "title": node["title"],
                "content": result,
                "word_count": len(result.replace(" ", "")),
                "article_count": 2,
                "text_count": 400,
            }

        agent._revise_section = fake_revise
        return agent, calls

    return _make


async def test_auto_fix_clears_placeholder_and_outline_ref(agent_factory):
    """表格占位 + 失效大纲引用：error 转定向 finding，一轮修订后复检清零。"""
    defective = (
        "# 1.1 架构设计\n\n本方案详见 9.9 节的部署架构。\n\n"
        "| GPU 型号 | （请补充） |\n| --- | --- |\n| 推理卡 | A10 |"
    )
    fixed = "# 1.1 架构设计\n\n本方案详见 1.2 节的部署架构。\n\n| GPU 型号 | A10（建议值） |\n| --- | --- |\n| 推理卡 | A10 |"
    agent, calls = agent_factory(lambda node_id, current, findings: fixed if "（请补充）" in current else current)
    sections = [_section("1.1", "架构设计", defective), _section("1.2", "部署方案", "# 1.2 部署方案\n\n内网部署。")]

    final, checks, info = await agent.auto_fix_machine_errors(
        spec=_spec(), requirements=None, analysis=None, sections=sections
    )

    assert info["initial_errors"] == 2
    assert info["rounds"] == 1
    assert info["remaining_errors"] == 0
    assert info["fixed_total"] == 2
    assert info["revised_nodes"] == ["1.1"]
    assert [c["node_id"] for c in calls] == ["1.1"]
    # finding 带机检类型与修复指令（修订代理据此改写）
    joined = "".join(str(f.get("where") or "") + f["issue"] for f in calls[0]["findings"])
    assert "机检（chart_placeholder）" in joined
    assert "机检（invalid_outline_ref）" in joined
    assert "删除图表" in joined
    # 返回的是修订后内容（供 summary 复算字数/图表统计）
    assert "（请补充）" not in final[0]["content"]
    assert not [c for c in checks if c["severity"] == "error"]


async def test_auto_fix_stance_conflict_sends_ruling_to_all_affected(agent_factory):
    """跨章口径冲突：裁决口径定向下发全部涉事章（开放派改掉即清零），并注入全稿基准。"""
    open_side = "# 1.1 架构设计\n\n本地算力不足时降级至在线服务承接推理请求。"
    restricted_side = "# 1.2 部署方案\n\n业务数据不出内网，在线通道默认关闭。"

    def revise(node_id, current, findings):
        if node_id == "1.1":
            return "# 1.1 架构设计\n\n本地算力不足时经采购人授权后可启用在线通道补充，默认不启用。"
        return current

    agent, calls = agent_factory(revise)
    sections = [_section("1.1", "架构设计", open_side), _section("1.2", "部署方案", restricted_side)]

    final, checks, info = await agent.auto_fix_machine_errors(
        spec=_spec(), requirements=None, analysis=None, sections=sections
    )

    assert info["initial_errors"] >= 1
    assert info["remaining_errors"] == 0
    assert sorted(c["node_id"] for c in calls) == ["1.1", "1.2"]
    for call in calls:
        issue = "".join(f["issue"] for f in call["findings"])
        assert "统一裁决为" in issue
        assert "默认关闭" in issue  # 裁决口径全文下发
        assert call["global"]  # 裁决同步注入全稿口径基准


async def test_auto_fix_stops_at_round_cap(agent_factory):
    """修订不收敛时按轮次上限停止，如实报告残留（前端给重生成指引）。"""
    defective = "# 1.1 架构设计\n\n| GPU 型号 | （请补充） |\n| --- | --- |"
    agent, calls = agent_factory(lambda node_id, current, findings: current)  # 原样返回=不收敛
    sections = [_section("1.1", "架构设计", defective)]

    final, checks, info = await agent.auto_fix_machine_errors(
        spec=_spec(), requirements=None, analysis=None, sections=sections
    )

    assert info["rounds"] == wiz.AUTO_FIX_MAX_ROUNDS
    assert len(calls) == wiz.AUTO_FIX_MAX_ROUNDS  # 每轮只重修仍报错的章
    assert info["remaining_errors"] == info["initial_errors"]
    assert info["fixed_total"] == 0
    assert final[0]["content"] == defective  # 修订失败/无效不丢原文


async def test_auto_fix_env_kill_switch(agent_factory, monkeypatch):
    """应急开关回到「只报告」旧行为：不发起任何修订。"""
    monkeypatch.setenv("BID_WIZARD_AUTO_FIX_ENABLED", "0")
    defective = "# 1.1 架构设计\n\n| GPU 型号 | （请补充） |\n| --- | --- |"
    agent, calls = agent_factory(lambda node_id, current, findings: current)
    sections = [_section("1.1", "架构设计", defective)]

    final, checks, info = await agent.auto_fix_machine_errors(
        spec=_spec(), requirements=None, analysis=None, sections=sections
    )

    assert info["enabled"] is False
    assert calls == []
    assert info["remaining_errors"] == info["initial_errors"] >= 1
    assert any(c["severity"] == "error" for c in checks)


async def test_auto_fix_noop_when_clean(agent_factory):
    """无 error 时机检照常返回，零修订调用。"""
    clean = "# 1.1 架构设计\n\n本方案由本地推理集群承载全部解析与生成负载，部署于采购人内网机房。"
    agent, calls = agent_factory(lambda node_id, current, findings: current)
    sections = [_section("1.1", "架构设计", clean)]

    final, checks, info = await agent.auto_fix_machine_errors(
        spec=_spec(), requirements=None, analysis=None, sections=sections
    )

    assert calls == []
    assert info["rounds"] == 0
    assert info["initial_errors"] == 0
    assert not [c for c in checks if c["severity"] == "error"]


async def test_auto_fix_paired_duplication_hint(agent_factory):
    """父章与直属子节同名标题：finding 带导语形态约束（防修订把父章扩写成全章）。"""
    parent = "# 1 总体方案\n\n本章概述整体设计。\n\n## 架构设计\n\n此处重复展开架构设计内容。"
    child = "# 1.1 架构设计\n\n分层架构说明。"
    agent, calls = agent_factory(lambda node_id, current, findings: "已修复")
    sections = [_section("1", "总体方案", parent), _section("1.1", "架构设计", child)]

    _, _, info = await agent.auto_fix_machine_errors(
        spec=_spec(), requirements=None, analysis=None, sections=sections
    )

    dup_calls = [c for c in calls if c["node_id"] == "1"]
    assert dup_calls, "paired_duplication 应触发父章修订"
    joined = "".join(str(f.get("where") or "") + f["issue"] for f in dup_calls[0]["findings"])
    assert "机检（paired_duplication）" in joined
    assert "导语" in joined
