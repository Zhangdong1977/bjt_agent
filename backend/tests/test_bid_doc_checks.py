# -*- coding: utf-8 -*-
"""组装期全文档机检（P1）单测：围栏配平/空章节/重复标题/模板残留/术语口径。

wizard 撰写期 draft_checks（23 号 O1/O6）单测：成对重复（2026-10-22 生产工单类型）、
叶子节篇幅达标、内容缺失、既有五项全稿检查复用。
"""
from backend.agent.bid_doc_checks import (
    assemble_checks,
    draft_checks,
    strip_leading_title_headings,
)


def test_clean_document_passes():
    md = (
        "# 技术方案\n\n我司针对本项目提供完整的技术实现路径，覆盖平台部署、定制开发、"
        "联调测试与试运行保障，整体方案与评分办法逐项对应，正文长度足够。\n\n"
        "# 运维保障方案\n\n提供 7×24 小时运维支持，建立三级响应机制，正文内容同样足够长以满足检查阈值要求。\n"
    )
    assert assemble_checks(md) == []


def test_unbalanced_fence_reported():
    md = "# 标题\n\n正文足够长足够长足够长足够长足够长。\n\n```mermaid\nflowchart TD\nA-->B\n"
    issues = assemble_checks(md)
    assert [i["type"] for i in issues] == ["fence_balance"]


def test_empty_section_reported():
    md = (
        "# 有内容的章节\n\n这段正文有足够的字数用于通过空章节检查，确保不会误报空章节问题。\n\n"
        "# 空章节\n\n# 又一个正常章节\n\n这段正文同样有足够字数，确保检查逻辑不会在这里产生误报项。\n"
    )
    types = [i["type"] for i in assemble_checks(md)]
    assert "empty_section" in types
    detail = next(i["detail"] for i in assemble_checks(md) if i["type"] == "empty_section")
    assert "空章节" in detail


def test_duplicate_heading_reported():
    md = (
        "# 售后服务承诺\n\n正文一的内容足够长足够长足够长足够长足够长足够长。\n\n"
        "# 售后服务承诺\n\n正文二的内容足够长足够长足够长足够长足够长足够长。\n"
    )
    detail = next(i for i in assemble_checks(md) if i["type"] == "duplicate_heading")
    assert "2 次" in detail["detail"]


def test_placeholder_leftover_reported():
    md = "# 章节\n\n正文包含 TODO 与 XXX 残留标记，长度足够长度足够长度足够长度足够。\n"
    pats = {i["detail"].split("×")[0].strip("'“") for i in assemble_checks(md) if i["type"] == "placeholder_leftover"}
    assert len(pats) >= 2  # TODO 与 XXX 都被抓到


def test_term_consistency_reported():
    md = ("# 公司简介\n\n我司成立于多年前，我司团队稳定。正文长度足够长度足够长度足够长度足够长度。\n\n"
          "# 服务承诺\n\n我公司承诺提供长期服务，我公司响应及时。正文长度足够长度足够长度足够。\n")
    detail = next(i for i in assemble_checks(md) if i["type"] == "term_consistency")
    assert "我司" in detail["detail"] and "我公司" in detail["detail"]


def test_issue_list_capped_at_20():
    headings = "\n\n".join("# 重复标题\n\n正文足够长正文足够长正文足够长正文足够长。" for _ in range(30))
    assert len(assemble_checks(headings)) == 20


# ------------------------------------------------------------------ draft_checks（23 号 O1/O6）


def _leaf_body(times: int = 60) -> str:
    return "正文内容" * times


def test_draft_checks_clean_sections_pass():
    sections = [
        {
            "node_id": "1",
            "title": "总体方案",
            "content": "# 总体方案\n\n本章定位与结构预告，导语正文长度超过空章节阈值，确保不会误报。",
            "word_count": 40,
        },
        {
            "node_id": "1.1",
            "title": "架构设计",
            "content": f"## 1.1 架构设计\n\n{_leaf_body(80)}",
            "word_count": 400,
            "article_count": 2,
            "text_count": 300,
        },
    ]
    assert draft_checks(sections) == []


def test_draft_checks_detects_paired_duplication():
    """2026-10-22 生产工单类型：父章正文里出现与直属子节同名的（迷你）标题行。"""
    sections = [
        {
            "node_id": "2",
            "title": "实施方案",
            "content": (
                "# 实施方案\n\n本章导语。\n\n## 2.1 数据迁移方案\n\n"
                "迷你版小节正文，与 2.1 子节内容成对重复。"
            ),
            "word_count": 100,
        },
        {
            "node_id": "2.1",
            "title": "数据迁移方案",
            "content": f"## 2.1 数据迁移方案\n\n{_leaf_body(60)}",
            "word_count": 300,
            "article_count": 2,
            "text_count": 200,
        },
    ]
    issues = draft_checks(sections)
    paired = [i for i in issues if i["type"] == "paired_duplication"]
    assert len(paired) == 1
    assert paired[0]["severity"] == "error" and paired[0]["node_id"] == "2"
    assert "数据迁移方案" in paired[0]["detail"]


def test_draft_checks_flags_short_leaf_but_not_parent():
    """叶子节篇幅不足告警；父章（导语）不按子节规划的篇幅要求。"""
    sections = [
        {
            "node_id": "1",
            "title": "大章",
            "content": "# 大章\n\n本章导语，长度足够避免空章节误报，不参与篇幅检查。",
            "word_count": 30,
            "article_count": 8,
            "text_count": 800,
        },
        {
            "node_id": "1.1",
            "title": "小节",
            "content": f"## 1.1 小节\n\n{'短' * 40}",
            "word_count": 40,
            "article_count": 5,
            "text_count": 400,
        },
    ]
    issues = draft_checks(sections)
    short = [i for i in issues if i["type"] == "short_section"]
    assert len(short) == 1 and short[0]["node_id"] == "1.1" and short[0]["severity"] == "warning"


def test_draft_checks_missing_content_is_error():
    issues = draft_checks([{"node_id": "3", "title": "空章", "content": "   ", "word_count": 0}])
    assert any(i["type"] == "missing_content" and i["severity"] == "error" for i in issues)


def test_draft_checks_reuses_assemble_checks_on_joined_md():
    sections = [
        {"node_id": "1", "title": "甲", "content": "# 甲\n\n```mermaid\nflowchart TD\nA-->B", "word_count": 20},
    ]
    assert any(i["type"] == "fence_balance" for i in draft_checks(sections))


def test_draft_checks_empty_input_returns_empty():
    assert draft_checks([]) == []


# ------------------------------------------------------------------ 2026-10-23 预发布实测缺陷回归


def _noise_md(sections: int = 25) -> str:
    """拼接标题行后紧跟正文小节标题的成稿模式：每节制造一条 empty_section 假阳性。"""
    parts = [
        f"# 章节{i}\n\n# 小节{i}\n\n正文内容{i}足够长足够长足够长，确保小节正文不触发空章节检查阈值。" for i in range(sections)
    ]
    parts.append("# 售后服务\n\n售后服务正文一足够长足够长足够长足够长足够长足够长。")
    parts.append("# 售后服务\n\n售后服务正文二足够长足够长足够长足够长足够长足够长。")
    return "\n\n".join(parts)


def test_assemble_checks_cap_drops_real_issues_without_skip():
    """旧缺陷复现：empty_section 噪音占满 20 条封顶配额，duplicate_heading 被整体挤掉。"""
    md = _noise_md()
    assert "duplicate_heading" not in [i["type"] for i in assemble_checks(md)]


def test_assemble_checks_skip_types_filters_before_cap():
    md = _noise_md()
    skipped = assemble_checks(md, skip_types=("empty_section",))
    types = [i["type"] for i in skipped]
    assert "duplicate_heading" in types
    assert "empty_section" not in types


def test_draft_checks_reports_duplicate_despite_heading_noise():
    """2026-10-23 预发布 888724b2 实测：60 节成稿 3 处双标题被噪音挤成 doc_checks=0。"""
    sections = [
        {
            "node_id": str(i + 1),
            "title": f"章节{i}",
            "content": f"# 章节{i}\n\n### 一、第{i}章总述\n\n正文内容{i}足够长，保证小节正文越过空章节阈值不被误判。",
            "word_count": 60,
            "article_count": 1,
            "text_count": 100,
        }
        for i in range(25)
    ]
    sections.append(
        {
            "node_id": "26",
            "title": "总体架构设计",
            "content": f"# 总体架构设计\n\n## 总体架构设计\n\n### 一、架构思路\n\n{_leaf_body(60)}",
            "word_count": 300,
            "article_count": 2,
            "text_count": 300,
        }
    )
    issues = draft_checks(sections)
    types = [i["type"] for i in issues]
    assert "duplicate_heading" in types
    assert "empty_section" not in types
    detail = next(i for i in issues if i["type"] == "duplicate_heading")["detail"]
    assert "总体架构设计" in detail and "2 次" in detail


def test_strip_leading_title_headings():
    title = "总体架构设计"
    # 同名（Markdown 层级可不同）开头标题被剥掉
    assert strip_leading_title_headings("## 总体架构设计\n\n### 一、思路\n\n正文", title) == "### 一、思路\n\n正文"
    # 编号前缀归一后同名也剥
    assert strip_leading_title_headings("### 2.1 总体架构设计\n\n正文", title) == "正文"
    # 双重同名连排都剥
    assert strip_leading_title_headings("## 总体架构设计\n\n## 总体架构设计\n\n正文", title) == "正文"
    # 加粗行同名也剥
    assert strip_leading_title_headings("**总体架构设计**\n\n正文", title) == "正文"
    # 开头标题不同名 / 正文直接开头：原样保留
    other = "### 一、总体思路\n\n正文"
    assert strip_leading_title_headings(other, title) == other
    plain = "正文开头没有标题"
    assert strip_leading_title_headings(plain, title) == plain


def test_draft_checks_flags_overlong_leaf():
    """O6 补口：叶子节篇幅超规划 2 倍提示（篇幅与费用防护，只提示不阻断）。"""
    sections = [
        {
            "node_id": "1.1",
            "title": "架构设计",
            "content": f"## 1.1 架构设计\n\n{_leaf_body(80)}",
            "word_count": 1500,
            "article_count": 2,
            "text_count": 300,
        }
    ]
    issues = draft_checks(sections)
    over = [i for i in issues if i["type"] == "overlong_section"]
    assert len(over) == 1 and over[0]["node_id"] == "1.1" and over[0]["severity"] == "warning"
    # 1.5 倍在允许范围内不报
    sections[0]["word_count"] = 900
    assert not [i for i in draft_checks(sections) if i["type"] == "overlong_section"]


# ------------------------------------------------------------------ O4 口径卡驱动机检（2026-10-23）


def test_assemble_checks_style_card_self_reference_and_forbidden_words():
    """O4：口径卡指定统一称谓后，其余自称变体并现即提示；禁用词出现即提示。"""
    md = "我司提供方案。我方团队实施。本公司负责售后。"  # 三种自称并现（无「我公司」）
    issues = assemble_checks(md, style_card={"self_reference": "我司"})
    term = [i for i in issues if i["type"] == "term_consistency"]
    assert len(term) == 1
    assert "统一称谓为「我司」" in term[0]["detail"]
    assert "我方" in term[0]["detail"] and "本公司" in term[0]["detail"]

    # 全稿只用统一称谓：无提示
    assert not [i for i in assemble_checks("我司实施。我司售后。", style_card={"self_reference": "我司"}) if i["type"] == "term_consistency"]
    # 未用统一称谓但两种变体并现：仍提示（混用本身就是问题）
    mixed = assemble_checks("我方实施。本公司售后。", style_card={"self_reference": "我司"})
    assert any(i["type"] == "term_consistency" for i in mixed)
    # 称谓不在候选池：不触发口径卡检查（归一层已挡，机检容错）
    assert not [i for i in assemble_checks(md, style_card={"self_reference": "咱家"}) if i["type"] == "term_consistency"]

    forbidden = assemble_checks("本系统现有支持 OCR 能力。", style_card={"self_reference": "我司", "forbidden_words": ["现有支持"]})
    hit = [i for i in forbidden if i["type"] == "forbidden_word"]
    assert len(hit) == 1 and "现有支持" in hit[0]["detail"]
    # 禁词未出现不提示；空禁词忽略
    assert not [i for i in assemble_checks("本系统支持 OCR。", style_card={"forbidden_words": ["现有支持", ""]}) if i["type"] == "forbidden_word"]


def test_assemble_checks_without_style_card_keeps_legacy_pairs():
    """不传口径卡=V1 既有行为：硬编码「我司/我公司」同义对，新增变体不触发。"""
    legacy = assemble_checks("我司实施，我公司售后。")
    assert any(i["type"] == "term_consistency" for i in legacy)
    # 「我方/本公司」不在硬编码对里：默认参数下不报（V1 assemble_checks 零变化）
    assert not [i for i in assemble_checks("我方实施，本公司售后。") if i["type"] == "term_consistency"]


def test_draft_checks_passes_style_card_through():
    """draft_checks 透传口径卡（wizard 全稿机检的 O4 接线）。"""
    sections = [
        {
            "node_id": "1",
            "title": "方案",
            "content": "## 1 方案\n\n我司建设，我方运维。系统现有支持全部要求。",
            "word_count": 30,
            "article_count": 1,
            "text_count": 100,
        }
    ]
    issues = draft_checks(sections, style_card={"self_reference": "我司", "forbidden_words": ["现有支持"]})
    types = {i["type"] for i in issues}
    assert "term_consistency" in types and "forbidden_word" in types
    # 不传口径卡：禁词检查不生效、称谓回到硬编码对
    legacy_types = {i["type"] for i in draft_checks(sections)}
    assert "forbidden_word" not in legacy_types


# ---------------------------------------------------------------- AI 输出痕迹（2026-10-24）

def test_ai_flavor_markers_flagged_per_section():
    from backend.agent.bid_doc_checks import draft_checks

    sections = [
        {
            "node_id": "1",
            "title": "总体方案",
            "word_count": 800,
            "content": (
                "好的，以下是本项目总体方案的介绍。首先，我们来看系统架构。\n\n"
                "本系统采用微服务架构，服务可用性不低于 99.9%，具体部署安排见下表。"
            ),
        }
    ]
    issues = draft_checks(sections)
    flavors = [i for i in issues if i["type"] == "ai_flavor"]
    markers = {i["detail"].split("「")[1].split("」")[0] for i in flavors}
    # 对话开头/写作说明均命中；每个短语一条、按章定位
    assert {"好的，", "以下是"} <= markers
    assert all(i["severity"] == "warning" and i["node_id"] == "1" for i in flavors)
    # 全稿级（node_id 空）不应重复报 ai_flavor（draft 逐章定位，拼合层跳过）
    assert not [i for i in flavors if not i["node_id"]]


def test_ai_flavor_clean_bid_text_zero_hits():
    from backend.agent.bid_doc_checks import assemble_checks, draft_checks

    clean = (
        "## 1 总体方案\n\n"
        "本系统采用微服务架构部署，服务可用性不低于 99.9%。我司承诺中标后 30 日内完成部署。\n\n"
        "具体设备配置参数如下表所示：\n\n| 组件 | 规格 | 数量 |\n| --- | --- | --- |\n| 应用服务器 | 32C128G | 2 |\n"
    )
    assert assemble_checks(clean) == []
    sections = [{"node_id": "1", "title": "总体方案", "word_count": 800, "content": clean}]
    assert [i for i in draft_checks(sections) if i["type"] == "ai_flavor"] == []


def test_ai_flavor_assemble_global_for_v1_path():
    from backend.agent.bid_doc_checks import assemble_checks

    md = "## 1 方案\n\n综上所述，本方案能够满足招标要求。作为AI，我无法提供真实业绩。"
    types = [i["type"] for i in assemble_checks(md)]
    assert "ai_flavor" in types
    details = " ".join(i["detail"] for i in assemble_checks(md) if i["type"] == "ai_flavor")
    assert "综上所述" in details and "作为AI" in details
