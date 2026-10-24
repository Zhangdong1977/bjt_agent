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

# 口径卡（23 号 O4）驱动：统一称谓候选池——style_card.self_reference 指定其一后，
# 其余变体与统一称谓（或变体彼此）在同一份标书内并现即告警
_SELF_REFERENCE_VARIANTS = ("我司", "我公司", "我方", "本公司")

# AI 输出痕迹（2026-10-24 用户反馈：思考过程/解释写法/对话语气不应出现在正文）——
# 高置信短语层报警，宁漏勿误：只收几乎必为 AI 痕迹的表述，"我们"等单词级不查
# （素材引用会误伤）；"综上所述/总而言之"等模板腔与措辞铁律双保险。warning 级只提示。
_AI_FLAVOR_MARKERS = (
    "作为AI", "作为人工智能", "作为语言模型", "AI助手",
    "好的，", "以下是", "接下来我们", "让我们", "我来为您", "我们将为您",
    "希望以上", "对您有所帮助", "本章将介绍", "本节将介绍", "下面我们从",
    "综上所述", "总而言之", "众所周知", "毋庸置疑", "我们相信",
)


def _ai_flavor_marker_hits(text: str) -> list[tuple[str, int]]:
    """命中的 AI 痕迹短语与次数（每个短语一条，避免同类刷屏）。"""
    return [(marker, text.count(marker)) for marker in _AI_FLAVOR_MARKERS if marker in text]


def _style_card_consistency_issues(joined: str, style_card: dict | None) -> list[dict[str, str]]:
    """口径卡驱动的术语/禁词检查（23 号 O4）：统一称谓混用 + 禁用词出现。

    style_card 结构见 bid_wizard_agent.normalize_style_card；None/非 dict 时无口径卡
    行为（assemble_checks 回退到硬编码 _SYNONYM_PAIRS）。只报告不改写。
    """
    if not isinstance(style_card, dict):
        return []
    issues: list[dict[str, str]] = []
    chosen = str(style_card.get("self_reference") or "").strip()
    if chosen in _SELF_REFERENCE_VARIANTS:
        used = [(term, joined.count(term)) for term in _SELF_REFERENCE_VARIANTS if joined.count(term)]
        mixed_others = [(term, count) for term, count in used if term != chosen]
        if len(used) >= 2 and mixed_others:
            detail_terms = "、".join(f"「{term}」×{count}" for term, count in mixed_others)
            issues.append({
                "type": "term_consistency",
                "detail": f"统一称谓为「{chosen}」，但存在 {detail_terms} 混用，建议全稿统一口径",
            })
    for raw in style_card.get("forbidden_words") or []:
        word = str(raw or "").strip()
        if not word:
            continue
        count = joined.count(word)
        if count:
            issues.append({
                "type": "forbidden_word",
                "detail": "口径卡禁用词「%s」出现 %d 次——按措辞红线应改写（如改为方案交付表述）" % (word[:40], count),
            })
    return issues

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_BOLD_LINE_RE = re.compile(r"^\*\*(.+?)\*\*\s*$")
# 标题归一：剥编号前缀（"5.3.1 "、"三、"）与空白/加粗标记，用于标题比对
_NUM_PREFIX_RE = re.compile(r"^(?:\d+(?:[.．]\d+)*|[一二三四五六七八九十]+)[、.．\s:：\-—]*")
_MAX_ISSUES = 20


def assemble_checks(
    md: str, skip_types: tuple[str, ...] = (), style_card: dict | None = None
) -> list[dict[str, str]]:
    """纯文本全文档机检；返回问题列表（空列表=全部通过）。

    skip_types：在封顶前丢弃的检查类型。wizard draft_checks 用它排除 empty_section——
    拼接的章节标题行后紧跟正文小节标题会产生大量空章节假阳性，若截断后才过滤，
    噪音会占满 _MAX_ISSUES 配额、把 duplicate_heading 等真问题整体挤掉
    （2026-10-23 预发布实测：60 节全稿 3 处双标题被挤成 doc_checks=0）。
    style_card（23 号 O4）：口径卡驱动术语/禁词检查替代硬编码同义对；不传时保持
    既有行为（V1 assemble_checks 无口径卡，默认参数不变）。
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

    # 5) 术语口径混用：有口径卡（O4）走口径卡驱动检查；否则维持硬编码同义对
    if isinstance(style_card, dict):
        issues.extend(_style_card_consistency_issues(joined, style_card))
    else:
        for a, b in _SYNONYM_PAIRS:
            ca, cb = joined.count(a), joined.count(b)
            if ca and cb:
                issues.append({
                    "type": "term_consistency",
                    "detail": "「%s」(%d 次) 与「%s」(%d 次) 混用，建议统一口径" % (a, ca, b, cb),
                })

    # 6) AI 输出痕迹：思考/解释写法/对话语气（wizard draft_checks 会逐章定位并跳过此处，避免重复）
    for marker, cnt in _ai_flavor_marker_hits(joined):
        issues.append({
            "type": "ai_flavor",
            "detail": "发现 AI 输出痕迹「%s」× %d——思考/解释写法/对话语气不应出现在投标正文" % (marker, cnt),
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


# 跨章口径组合告警（2026-10-24 质量轮：在线算力/数据出内网在成稿里三套说法并存，
# 直触★保密义务与验收承诺）：同一主题的「开放类」与「受限类」表述分别落在不同章节时
# 报全稿级 error。开放类词必须自带「在线/算力」语境（第 2 轮质量轮教训：裸「自动降级」
# 把进度推送通道降级误归因在线算力口径）；ruling=检出冲突后下发给涉事章节的裁决口径
# （保守方向可兑现：默认关闭+书面授权，降级只作为授权后的应对手段描述）。
_STANCE_TOPIC_PATTERNS = (
    {
        "topic": "在线模型通道与数据出内网",
        "open": (
            "降级至在线", "降级到在线", "降级调用在线", "先以在线算力", "在线算力承接",
            "以在线算力补充", "在线算力为补充",
        ),
        "restricted": ("默认关闭", "不将业务数据外发", "不外发", "不出内网", "书面授权"),
        "ruling": (
            "在线模型通道默认关闭，不自动降级启用；确需启用须先取得采购人书面授权、"
            "经数据脱敏与出口白名单，启用与降级动作在管理端留痕可查；未经授权业务数据不出采购人内网。"
            "本地算力不足时的应对写为「经授权后可启用在线通道补充」，不写自动降级。"
        ),
    },
)


# 开放类命中的否定式豁免（2026-10-24 终审：2.5「不自动降级至在线服务」被「降级至在线」
# 子串命中，把受限派标准表述误判成开放派）——命中点前 8 字符内出现否定词即不算开放口径。
# 「不足/不但/不仅…」是否定词的常见假朋友（"本地算力不足时降级"恰是开放口径），
# 用负向先行排除。宁漏勿误：漏检的真开放表述由审读轮兜底，误报会卡死全稿门禁。
_STANCE_NEGATION_PREFIX_RE = re.compile(
    r"(?:避免|防止|绝不|不得|不(?!足|但|仅|过|断|乏|论|管)|非|未|无|禁|勿|莫)[^。；;,\n]{0,8}$"
)


def _has_open_stance_hit(content: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        idx = content.find(pattern)
        while idx != -1:
            prefix = content[max(0, idx - 8):idx]
            if not _STANCE_NEGATION_PREFIX_RE.search(prefix):
                return True
            idx = content.find(pattern, idx + 1)
    return False


def stance_conflict_targets(sections: list[dict]) -> list[dict]:
    """检出冲突的主题组：[{topic, ruling, open_nodes, restricted_nodes}]。

    开放类/受限类分别命中不同章节才算冲突（同一章内两类并存多为「默认关闭＋授权后
    可启用」的自洽限定）；供审读-修订链路把裁决口径定向下发给涉事章节。
    """
    targets: list[dict] = []
    for group in _STANCE_TOPIC_PATTERNS:
        open_nodes: set[str] = set()
        restricted_nodes: set[str] = set()
        for section in sections:
            content = str(section.get("content") or "")
            if not content.strip():
                continue
            if _has_open_stance_hit(content, group["open"]):
                open_nodes.add(str(section.get("node_id") or ""))
            if any(pattern in content for pattern in group["restricted"]):
                restricted_nodes.add(str(section.get("node_id") or ""))
        if (
            open_nodes
            and restricted_nodes
            and (open_nodes - restricted_nodes or restricted_nodes - open_nodes)
        ):
            targets.append(
                {
                    "topic": group["topic"],
                    "ruling": str(group.get("ruling") or ""),
                    "open_nodes": open_nodes,
                    "restricted_nodes": restricted_nodes,
                }
            )
    return targets


def _stance_conflict_issues(sections: list[dict]) -> list[dict[str, str]]:
    """跨章口径组合：开放类/受限类分别命中不同章节 → 一条全稿级 error。"""
    issues: list[dict[str, str]] = []
    for target in stance_conflict_targets(sections):
        issues.append({
            "severity": "error", "node_id": "", "type": "stance_conflict",
            "detail": "「%s」口径跨章并存两种说法（自动/降级使用在线算力：%s｜默认关闭/须授权不外发：%s）"
                      "——直触保密义务与验收承诺，须全稿统一为一种口径" % (
                target["topic"],
                "、".join(sorted(target["open_nodes"]))[:80],
                "、".join(sorted(target["restricted_nodes"]))[:80],
            ),
        })
    return issues


# 章节引用形态（精度优先、宁漏勿误）：显式「第X.Y…节」「详见/参见/见X.Y…」「章节X.Y…」，
# 以及「对应章节」表格列内的编号（★条款响应总表类表格的引用列）。「见X.Y」后紧跟「条」
# 的是招标条款号（如「见 1.2.3 条」）不是大纲引用，排除；(?![\d.]) 防编号被回溯截短
# （「见 1.2.3 条」排除整段失败时引擎会退而匹配「1.2」，第 2 轮质量轮实测）。
_OUTLINE_REF_PATTERNS = (
    re.compile(r"第\s*(\d{1,2}(?:\.\d{1,2})+)\s*节"),
    re.compile(r"(?:详见|参见|见)\s*(\d{1,2}(?:\.\d{1,2})+)(?![\d.])(?!\s*条)"),
    re.compile(r"章节\s*(\d{1,2}(?:\.\d{1,2})+)"),
)
_OUTLINE_REF_TOKEN_RE = re.compile(r"\d{1,2}(?:\.\d{1,2})+")
_TABLE_ROW_RE = re.compile(r"^\s*\|")


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _outline_ref_issues(sections: list[dict], outline_ids: set[str]) -> list[dict[str, str]]:
    """显式引用的大纲编号必须命中大纲集合（或存在以其为编号前缀的节点）。

    「对应章节」表格只收该列单元格内的编号——同表其他列（如「条款编号」列的 ★1.2.3
    招标条款号）不是大纲引用，全行收集会误报（第 2 轮质量轮：1.2.3/1.2.4/1.2.7/4.3.1）。
    """
    issues: list[dict[str, str]] = []
    for section in sections:
        content = str(section.get("content") or "")
        if not content.strip():
            continue
        refs: list[str] = []
        in_ref_table = False
        ref_col = -1
        for line in content.split("\n"):
            if "对应章节" in line and _TABLE_ROW_RE.match(line):
                in_ref_table = True
                cells = _table_cells(line)
                ref_col = next(
                    (idx for idx, cell in enumerate(cells) if "对应章节" in cell), -1
                )
                continue  # 表头行本身不收编号
            if in_ref_table:
                if _TABLE_ROW_RE.match(line):
                    cells = _table_cells(line)
                    if 0 <= ref_col < len(cells):
                        refs.extend(_OUTLINE_REF_TOKEN_RE.findall(cells[ref_col]))
                else:
                    in_ref_table = False
            refs.extend(
                match.group(1) for pattern in _OUTLINE_REF_PATTERNS for match in pattern.finditer(line)
            )
        invalid = sorted({
            ref
            for ref in refs
            if ref not in outline_ids and not any(node_id.startswith(ref + ".") for node_id in outline_ids)
        })
        if invalid:
            issues.append({
                "severity": "error", "node_id": str(section.get("node_id") or ""),
                "type": "invalid_outline_ref",
                "detail": "正文引用了大纲中不存在的大纲编号 %s——核对指向实际存在的章节（编号须与大纲一致）"
                          % "、".join(invalid)[:60],
            })
    return issues


# 图表内占位（第 2 轮质量轮：2.2.3 架构图节点标签与部署表 7 处「请补充」审读点名未修）：
# mermaid 图节点/表格单元格内的占位直接以"未完成方案"观感交付给评标专家，error 级驱动修订
# ——能确定建议值的写建议值（如 GPU 显存/型号参考），确属待投标人补充的移出图表放正文占位。
_CHART_PLACEHOLDER_TOKEN = "请补充"


def chart_placeholder_issues(sections: list[dict]) -> list[dict[str, str]]:
    """图表内占位（公开供审读-修订链路把 error 转为定向修订 finding，第 3 轮质量轮）。"""
    issues: list[dict[str, str]] = []
    for section in sections:
        content = str(section.get("content") or "")
        if _CHART_PLACEHOLDER_TOKEN not in content:
            continue
        hits = 0
        in_fence = False
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if (in_fence or _TABLE_ROW_RE.match(line)) and _CHART_PLACEHOLDER_TOKEN in line:
                hits += line.count(_CHART_PLACEHOLDER_TOKEN)
        if hits:
            issues.append({
                "severity": "error", "node_id": str(section.get("node_id") or ""),
                "type": "chart_placeholder",
                "detail": "图表（mermaid 节点标签/表格单元格）内出现「请补充」×%d——图表占位交付"
                          "观感为方案未完成：能确定建议值的写建议值（可注明按采购人环境调整），"
                          "确属待投标人补充的移出图表、在正文占位" % hits,
            })
    return issues


# 无出处产能数据（第 3 轮质量轮：1.1「4 个工作日内形成 301 页正文、215 个标题、232 张表格、
# 40 幅插图、14 万字以上」三轮审读结论摇摆后回归——沉淀为永久机检；warning 级人工确认，
# 素材里真实存在的业绩数字由人分辨，机检只兜「N 个工作日 + N 页/表/字」自证句式）。
_UNSOURCED_METRICS_RE = re.compile(
    r"[^\n。；！？]{0,40}\d+\s*个?工作日[^\n。；！？]{0,60}\d{2,6}\s*(?:页|万字|字以上|张表格|张表|幅插图|个标题)"
)


def _unsourced_metrics_issues(sections: list[dict]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for section in sections:
        content = str(section.get("content") or "")
        if not content:
            continue
        hits = _UNSOURCED_METRICS_RE.findall(content)
        if hits:
            issues.append({
                "severity": "warning", "node_id": str(section.get("node_id") or ""),
                "type": "unsourced_metrics",
                "detail": "发现「N 个工作日产出 N 页/表/字」式产能数据 %d 处——无素材出处的自证数字"
                          "无法举证又易被质疑夸大，确认素材可佐证或删除（措辞铁律⑥）" % len(hits),
            })
    return issues


def draft_checks(
    sections: list[dict],
    style_card: dict | None = None,
    outline_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """wizard 撰写完成期全稿机检（逐章 md 已含各自标题行）。

    sections 每项：{node_id, title, content, word_count?, article_count?, text_count?}
    返回 [{severity, node_id, type, detail}]（node_id 为空串=全稿级），封顶 30 条。

    style_card（23 号 O4）：口径卡驱动的统一称谓/禁用词检查（不传=硬编码同义对，兼容存量）。

    覆盖：既有五项全稿检查（拼合后复用 assemble_checks，empty_section 与 ai_flavor 在封顶前
    跳过——前者父章导语/「标题行后紧跟小节标题」的拼接模式会假阳性，后者逐章定位更可操作）
    + 逐章检查——
    ① 章内成对重复（2026-10-22 生产工单类型：父章正文里出现与直属子节同名的标题行）；
    ② 叶子节篇幅达标（实际字数 < 规划 article_count×text_count × 0.5 告警，
       > 2.0 提示超稿——篇幅与费用防护）；
    ③ 章节正文缺失/为空（content 空或过短）；
    ④ AI 输出痕迹（思考/解释写法/对话语气，2026-10-24 用户反馈——高置信短语、warning 级）
    + 全稿级检查（2026-10-24 质量轮）——
    ⑤ 跨章口径组合（在线模型通道/数据出内网等主题的开放类与受限类表述分布在不同章，
       error——直触保密与验收承诺）；
    ⑥ 大纲引用有效性（outline_ids 传入时启用：显式「第X.Y节/详见X.Y」及「对应章节」表格列
       中的编号须命中大纲 node_id 集合，未命中 error——评标核查入口不能指向不存在的章节）；
    ⑦ 图表内占位（mermaid 节点标签/表格单元格内的「请补充」，error——图表占位交付观感
       为方案未完成）；
    ⑧ 无出处产能数据（「N 个工作日产出 N 页/表/字」式自证句式，warning 人工确认——
       第 3 轮质量轮：同类问题三轮审读结论摇摆后回归，沉淀为永久规则）。
    """
    issues: list[dict[str, str]] = []
    valid = [s for s in sections if isinstance(s, dict) and str(s.get("node_id") or "")]
    if not valid:
        return issues

    joined = "\n\n".join(str(s.get("content") or "") for s in valid)
    # 空章节检查换成本模块逐节口径：父章与直属子节同批时只写导语（合法地短）、
    # 拼接标题后紧跟正文小节标题也合法——empty_section 让 assemble_checks 在封顶前跳过，
    # 防止 40+ 条噪音占满配额挤掉真问题；同等问题由下方 missing_content + short/overlong 覆盖。
    # ai_flavor 同理跳过拼合层（下方逐章定位更可操作，避免同一条痕既报全稿又报章节）
    for base in assemble_checks(
        joined, skip_types=("empty_section", "ai_flavor"), style_card=style_card
    ):
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

        # ④ AI 输出痕迹逐章定位（思考/解释写法/对话语气）——warning 只提示，按短语去重不刷屏
        for marker, cnt in _ai_flavor_marker_hits(content):
            issues.append(
                {"severity": "warning", "node_id": node_id, "type": "ai_flavor",
                 "detail": "「%s」出现 %d 次——思考/解释写法/对话式 AI 痕迹，建议删除或改为陈述句" % (marker, cnt)}
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

    # ⑤ 跨章口径组合（全稿级）；⑥ 大纲引用有效性（传 outline_ids 才启用，引用未生成
    # 章节的合法引用靠调用方传全量 spec node_id 区分）；⑦ 图表内占位（第 2 轮质量轮）；
    # ⑧ 无出处产能数据（第 3 轮质量轮）
    issues.extend(_stance_conflict_issues(valid))
    if outline_ids is not None:
        ids = {str(item) for item in outline_ids if str(item)}
        if ids:
            issues.extend(_outline_ref_issues(valid, ids))
    issues.extend(chart_placeholder_issues(valid))
    issues.extend(_unsourced_metrics_issues(valid))

    return issues[:_DRAFT_MAX_ISSUES]
