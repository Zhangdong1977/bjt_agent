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
_MAX_ISSUES = 20


def assemble_checks(md: str) -> list[dict[str, str]]:
    """纯文本全文档机检；返回问题列表（空列表=全部通过）。"""
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

    return issues[:_MAX_ISSUES]
