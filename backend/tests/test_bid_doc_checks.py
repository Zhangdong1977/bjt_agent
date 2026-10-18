# -*- coding: utf-8 -*-
"""组装期全文档机检（P1）单测：围栏配平/空章节/重复标题/模板残留/术语口径。"""
from backend.agent.bid_doc_checks import assemble_checks


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
