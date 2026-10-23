# -*- coding: utf-8 -*-
"""组装期全文档机检（P1）——assemble 阶段对 joined md 做纯文本机器检查。

方法论同应标项目机检脚本：规则零依赖、结果量化进任务 summary，供运营/用户
在交付前看到"哪些章节是空的、术语口径是否混用、围栏是否闭合"。只报告不改写
（写改由人工/上游环节决策），每类问题聚合成一条 {type, detail}，整体封顶 20 条。
"""
from __future__ import annotations

import re

# 模板/占位残留（"（请补充）"事实缺口已由 chart_quality_stats.fact_gaps 统计）
_PLACEHOLDER_PATTERNS = ("XXX", "TODO", "TBD", "{{", "【待补充】", "[待补充]")

# 术语口径：同义自称/称谓在同一份标书内混用是评标观感问题（只提示不强制）
_SYNONYM_PAIRS = (("我司", "我公司"),)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_BOLD_LINE_RE = re.compile(r"^\*\*(.+?)\*\*\s*$")
# 标题归一：剥编号前缀（"5.3.1 "、"三、"）与空白/加粗标记，用于标题比对
_NUM_PREFIX_RE = re.compile(r"^(?:\d+(?:[.．]\d+)*|[一二三四五六七八九十]+)[、.．\s:：\-—]*")
_MAX_ISSUES = 20


def assemble_checks(md: str, skip_types: tuple[str, ...] = ()) -> list[dict[str, str]]:
    """纯文本全文档机检；返回问题列表（空列表=全部通过）。

    skip_types：在封顶前丢弃的检查类型。wizard draft_checks 用它排除 empty_section——
    拼接的章节标题行后紧跟正文小节标题会产生大量空章节假阳性，若截断后才过滤，
    噪音会占满 _MAX_ISSUES 配额、把 duplicate_heading 等真问题整体挤掉
    （2026-10-23 预发布实测：60 节全稿 3 处双标题被挤成 doc_checks=0）。
    """
    issues: list[dict[str, str]] = []
    lines = (md or "").split("\n")

    # 1) 围栏配平：``` 开头行必须成对，落单=某图表围栏未闭合（会级联吞正文）
    fence_lines = [i for i, ln in enumerate(lines) if ln.lstrip().startswith("```")]
    if len(fence_lines) % 2:
        issues.append({
            "type": "fence_balance",
            "detail": "存在未闭合的 ``` 围栏（共 %d 个围栏行，奇数）——图表块可能吞掉后续正文" % len(fence_lines),
        })

    # 2) 空章节：标题后到下一个标题之间有效正文 < 30 字
    headings = [(i, m.group(2)) for i, ln in enumerate(lines) if (m := _HEADING_RE.match(ln))]
    for k, (idx, title) in enumerate(headings):
        end = headings[k + 1][0] if k + 1 < len(headings) else len(lines)
        body = "".join(lines[idx + 1:end])
        body = re.sub(r"\s|```|\W", "", body, flags=re.UNICODE)
        if len(body) < 30:
            issues.append({"type": "empty_section", "detail": "章节「%s」正文疑似为空（<30 字）" % title[:40]})

    # 3) 重复标题（跨章节完全同名，多为大纲/生成重复）
    seen: dict[str, int] = {}
    for _, title in headings:
        seen[title] = seen.get(title, 0) + 1
    for title, cnt in seen.items():
        if cnt > 1:
            issues.append({"type": "duplicate_heading", "detail": "标题「%s」出现 %d 次" % (title[:40], cnt)})

    # 4) 模板残留
    joined = "\n".join(lines)
    for pat in _PLACEHOLDER_PATTERNS:
        cnt = joined.count(pat)
        if cnt:
            issues.append({"type": "placeholder_leftover", "detail": "发现模板残留 %r × %d" % (pat, cnt)})

    # 5) 术语口径混用
    for a, b in _SYNONYM_PAIRS:
        ca, cb = joined.count(a), joined.count(b)
        if ca and cb:
            issues.append({
                "type": "term_consistency",
                "detail": "「%s」(%d 次) 与「%s」(%d 次) 混用，建议统一口径" % (a, ca, b, cb),
            })

    if skip_types:
        issues = [issue for issue in issues if issue["type"] not in skip_types]
    return issues[:_MAX_ISSUES]


# --------------------------------------------------------------------------- wizard 撰写期全稿机检（23 号 O1/O6）

# 只报告不改写；severity: error=建议重生成该章，warning=人工确认
_DRAFT_MAX_ISSUES = 30
# 叶子节篇幅低于规划值的该比例时告警（SOP check_leaf ≥500 字的产品化：按各章规划值比例而非硬编码）
_LEAF_WORD_RATIO = 0.5
# 叶子节篇幅高于规划值的该比例时提示（2026-10-23 预发布实测：表格+长文惯性让全稿到规划的 2 倍，
# 超稿无判据则篇幅与费用悄悄翻倍——只提示不阻断）
_LEAF_OVER_WORD_RATIO = 2.0


def _normalize_title(text: str) -> str:
    """标题归一（比对用）：去空白/加粗号 → 剥编号前缀 → 再去空白。"""
    stripped = re.sub(r"[\s*#]+", "", text or "")
    return _NUM_PREFIX_RE.sub("", stripped)


def _direct_children(node_id: str, all_ids: list[str]) -> list[str]:
    prefix = node_id + "."
    return [
        other
        for other in all_ids
        if other.startswith(prefix) and "." not in other[len(prefix):]
    ]


def strip_leading_title_headings(body: str, title: str) -> str:
    """剥掉正文开头与章节标题同名（归一比较）的标题行。

    拼装 content 时系统会拼接标题行，而模型时常忽略「不含本节标题」指令、
    正文又以同名标题开头——成稿连排两个同名标题（2026-10-23 预发布实测 60 节出现 3 处），
    同时给机检制造 duplicate_heading/empty_section 假信号。只处理正文最前面的连续
    同名标题段（#/加粗行，编号前缀归一），不触碰正文中间的任何标题；开头标题与
    章节标题不同名则原样返回。
    """
    target = _normalize_title(title)
    if not target:
        return body or ""
    lines = (body or "").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading_text = ""
        if (match := _HEADING_RE.match(line)):
            heading_text = match.group(2)
        elif (match := _BOLD_LINE_RE.match(line)):
            heading_text = match.group(1)
        if not heading_text or _normalize_title(heading_text) != target:
            break
        index += 1
    if not index:
        return body or ""
    return "\n".join(lines[index:]).lstrip("\n")


def draft_checks(sections: list[dict]) -> list[dict[str, str]]:
    """wizard 撰写完成期全稿机检（逐章 md 已含各自标题行）。

    sections 每项：{node_id, title, content, word_count?, article_count?, text_count?}
    返回 [{severity, node_id, type, detail}]（node_id 为空串=全稿级），封顶 30 条。

    覆盖：既有五项全稿检查（拼合后复用 assemble_checks，empty_section 在封顶前跳过——
    父章导语与「标题行后紧跟小节标题」的拼接模式都会触发空章节假阳性）+ 逐章检查——
    ① 章内成对重复（2026-10-22 生产工单类型：父章正文里出现与直属子节同名的标题行）；
    ② 叶子节篇幅达标（实际字数 < 规划 article_count×text_count × 0.5 告警，
       > 2.0 提示超稿——篇幅与费用防护）；
    ③ 章节正文缺失/为空（content 空或过短）。
    """
    issues: list[dict[str, str]] = []
    valid = [s for s in sections if isinstance(s, dict) and str(s.get("node_id") or "")]
    if not valid:
        return issues

    joined = "\n\n".join(str(s.get("content") or "") for s in valid)
    # 空章节检查换成本模块逐节口径：父章与直属子节同批时只写导语（合法地短）、
    # 拼接标题后紧跟正文小节标题也合法——empty_section 让 assemble_checks 在封顶前跳过，
    # 防止 40+ 条噪音占满配额挤掉真问题；同等问题由下方 missing_content + short/overlong 覆盖。
    for base in assemble_checks(joined, skip_types=("empty_section",)):
        issues.append(
            {"severity": "warning", "node_id": "", "type": base["type"], "detail": base["detail"]}
        )

    all_ids = [str(s.get("node_id")) for s in valid]
    by_id = {str(s.get("node_id")): s for s in valid}

    for section in valid:
        node_id = str(section.get("node_id"))
        title = str(section.get("title") or node_id)
        content = str(section.get("content") or "")
        if not content.strip():
            issues.append(
                {"severity": "error", "node_id": node_id, "type": "missing_content",
                 "detail": "章节「%s」没有可检查的正文" % title[:40]}
            )
            continue

        # ① 章内成对重复：父章正文中的标题行（#/加粗行）与直属子节标题同名
        child_titles = [
            _normalize_title(str(by_id.get(child, {}).get("title") or ""))
            for child in _direct_children(node_id, all_ids)
        ]
        child_titles = [t for t in child_titles if t]
        if child_titles:
            for ln in content.split("\n"):
                heading_text = ""
                if (m := _HEADING_RE.match(ln)):
                    heading_text = m.group(2)
                elif (m := _BOLD_LINE_RE.match(ln)):
                    heading_text = m.group(1)
                if not heading_text:
                    continue
                normalized = _normalize_title(heading_text)
                if normalized and normalized in child_titles:
                    issues.append(
                        {"severity": "error", "node_id": node_id, "type": "paired_duplication",
                         "detail": "父章正文出现与直属子节同名的标题行「%s」——同主题疑似写了两遍，建议重生成本章" % heading_text[:40]}
                    )

        # ② 叶子节篇幅达标
        is_leaf = not any(
            other.startswith(node_id + ".") for other in all_ids if other != node_id
        )
        if is_leaf:
            article = int(section.get("article_count") or 2)
            text = int(section.get("text_count") or 400)
            planned = max(article * text, 1)
            actual = int(section.get("word_count") or 0) or len(re.sub(r"\s", "", content))
            if actual < planned * _LEAF_WORD_RATIO:
                issues.append(
                    {"severity": "warning", "node_id": node_id, "type": "short_section",
                     "detail": "「%s」约 %d 字，仅为规划篇幅（约 %d 字）的 %d%%" % (
                         title[:40], actual, planned, int(actual * 100 / planned))}
                )
            elif actual > planned * _LEAF_OVER_WORD_RATIO:
                issues.append(
                    {"severity": "warning", "node_id": node_id, "type": "overlong_section",
                     "detail": "「%s」约 %d 字，达规划篇幅（约 %d 字）的 %d%%——确认表格与展开是否必要，超稿同步推高篇幅与费用" % (
                         title[:40], actual, planned, int(actual * 100 / planned))}
                )

    return issues[:_DRAFT_MAX_ISSUES]
