# -*- coding: utf-8 -*-
"""组装期全文档机检（P1）单测：围栏配平/空章节/重复标题/模板残留/术语口径。

wizard 撰写期 draft_checks（23 号 O1/O6）单测：成对重复（2026-10-22 生产工单类型）、
叶子节篇幅达标、内容缺失、既有五项全稿检查复用。
"""
from backend.agent.bid_doc_checks import assemble_checks, draft_checks


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
