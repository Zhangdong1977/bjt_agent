"""AI编标（bid-wizard）Agent：素材分段索引 / 招标要素 / 问卷 / Spec / 逐章撰写。

与 V1 bid_draft_agent 共享章节生成内核（JSON 容错解析、编号规范化、mermaid
钳制/闭合），以 import 方式复用、**不修改 V1 代码路径**（doc/workspace/20-bid-wizard.md §5.3）。

分工：
- 同步交互（问卷生成 / Spec 生成与修订）由 API 请求内直接调用本模块函数；
- 异步任务（素材索引 / 逐章撰写）由 backend/tasks/bid_wizard_tasks.py 的 celery 包装调用。

素材消费采用两阶段上下文注入：先把（相关素材的）index.md 注入 prompt 让 LLM
挑分段号，代码再读选中分段拼进正式调用——不做 embedding、不走 Mini-Agent
工具循环（决策 23；material_* 工具定义留待后续演进）。
"""

from __future__ import annotations

import json
import logging
import re
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import select

from backend.models import BidWizardMaterial, BidWizardSection, BidWritingTask, Document
from backend.services.llm_factory import create_llm_client
from backend.services.usage_recorder import instrument_llm_client

# ---- 共享内核：直接复用 V1 实现（V1 文件保持零改动） ----
from backend.agent.bid_draft_agent import (
    ANALYSIS_CONTEXT_MAX_CHARS,
    ANALYSIS_JSON_MAX_CHARS,
    ANALYSIS_SYSTEM_PROMPT,
    ANALYSIS_USER_TEMPLATE,
    OUTLINE_CONTEXT_MAX_CHARS,
    OUTLINE_MAX_NODES,
    SECTION_RESULT_MAX_CHARS,
    _bound_analysis,
    clamp_mermaid_blocks,
    close_unterminated_mermaid_fence,
    normalize_outline,
    parse_json_payload,
    strip_code_fence,
)

logger = logging.getLogger(__name__)

MATERIAL_CHUNK_MAX_CHARS = 1_500
MATERIAL_CHUNK_META_CONTEXT_CHARS = 600
MATERIAL_MAX_CHUNKS = 200
QUESTIONNAIRE_MAX_QUESTIONS = 15
SPEC_CONTEXT_MAX_CHARS = 16_000
MATERIAL_CONTEXT_MAX_CHARS = 12_000
# 粗估：中文约 0.7 token/字（deepseek 系 tokenizer 量级），仅作上传前提示，
# 实际计费按 ai_usage_records 真实用量结算。
ESTIMATE_TOKENS_PER_CHAR = 0.7

# ------------------------------------------------------------------ prompts

INDEX_META_SYSTEM_PROMPT = (
    "你是资料管理员。为素材的每个分段写一句摘要和几个关键词，供后续检索。"
    "只输出一个合法 JSON 数组，不要输出任何其他文字或代码块标记。"
)

INDEX_META_USER_TEMPLATE = """以下是素材的各分段（编号、标题、正文开头）：

__CHUNK_LIST__

请为每个分段输出一项 JSON：

[{"no": 1, "summary": "不超过 80 字的内容摘要", "keywords": ["关键词", "最多 5 个"]}]

要求：摘要必须基于该段原文，严禁推测段外内容；keywords 用短词。"""

QUESTIONNAIRE_SYSTEM_PROMPT = (
    "你是资深投标文件专家。基于招标要素与公司素材索引，设计一份结构化问卷，"
    "收集编写投标文件还缺的必要补充知识（如交付周期、报价策略、人员到岗、类似业绩口径等）。"
    "只输出一个合法 JSON 对象，不要输出任何其他文字或代码块标记。"
)

QUESTIONNAIRE_USER_TEMPLATE = """## 招标要素
__ANALYSIS_JSON__

## 公司素材索引（每行一段：编号｜标题｜摘要｜关键词）
__MATERIAL_INDEX__

请输出问卷 JSON：

{"questions": [
  {"topic": "主题（商务/技术/人员/业绩/交付/其他）",
   "question": "向投标人提出的问题，一句话",
   "why": "为什么需要这个信息（与招标要求的关联）",
   "suggested_answer": "建议答案：优先采用素材索引中出现的真实事实，并注明来源；素材中没有则按常见做法推断",
   "source": "素材依据（如 '素材名#3'），推断时填 '推断'",
   "inferred": false}
]}

规则：
1. 共 6-15 题，按主题分组排序，只问编写标书真正必需、且招标文件与素材里都没有的信息；
2. 能从素材索引直接回答的不要问，直接写进 suggested_answer 并给 source；
3. 素材没有依据的建议答案必须 inferred=true，提示用户确认；
4. 严禁在答案里虚构公司资质、业绩、人员与数据。"""

SPEC_SYSTEM_PROMPT = (
    "你是资深投标文件编写专家。根据编写需求、招标要素与素材索引，设计投标文件编写大纲（Spec）。"
    "只输出一个合法 JSON 数组，不要输出任何其他文字或代码块标记。"
)

SPEC_USER_TEMPLATE = """## 招标要素
__ANALYSIS_JSON__

## 编写需求（用户问卷确认）
__REQUIREMENTS_TEXT__

## 公司素材索引（每行一段：编号｜标题｜摘要｜关键词）
__MATERIAL_INDEX__

请输出 Spec JSON 数组（不超过 __MAX_NODES__ 个章节，层级 1-4 级），每项格式：

{"title": "章节标题", "level": 1,
 "summary": "本章内容要点摘要（1-3 句，写作时的执行纲领）",
 "article_count": 2, "text_count": 400,
 "charts": [{"type": "table|mermaid", "title": "图表标题", "points": "图表要呈现的要点"}]}

规则：
1. 大纲完整覆盖招标需求与评分标准对应的响应内容，与评分办法呼应；
2. summary 写清楚本章要回应什么、用什么素材支撑；
3. charts 按内容需要规划（每章 0-3 项）：对比/参数/人员/进度用 table，组织架构/流程/横道图/占比用 mermaid；
4. 素材里有真实业绩/资质可引用的章节，在 summary 中点明引用方向；严禁虚构。"""

REVISE_SYSTEM_PROMPT = (
    "你是资深投标文件编写专家。按用户的修改指令调整编写大纲（Spec），保持未涉及的部分不变。"
    "只输出一个合法 JSON 数组（完整的新大纲），不要输出任何其他文字或代码块标记。"
)

REVISE_USER_TEMPLATE = """当前 Spec：

__CURRENT_SPEC__

用户修改指令：
__INSTRUCTION__

请输出调整后的完整 Spec JSON 数组，字段格式与输入一致（title/level/summary/article_count/text_count/charts）。"""

SELECT_SYSTEM_PROMPT = (
    "你是素材检索员。根据任务描述，从素材索引中挑选需要引用原文的分段。"
    "只输出一个合法 JSON 对象，不要输出任何其他文字或代码块标记。"
)

SELECT_FLAT_USER_TEMPLATE = """## 素材索引（素材以【素材 文档ID｜文件名】分块，每行：编号｜标题｜摘要｜关键词）
__MATERIAL_INDEX__

## 任务
__INSTRUCTION__

请输出被选中分段的引用列表（格式 "文档ID#编号"）：

{"chunks": ["<文档ID>#<编号>"]}"""

SELECT_MAPPED_USER_TEMPLATE = """## 素材索引（素材以【素材 文档ID｜文件名】分块，每行：编号｜标题｜摘要｜关键词）
__MATERIAL_INDEX__

## 待撰写的章节（node_id: 标题 — 摘要）
__NODE_LIST__

请为每个章节挑选需要引用原文的分段（格式 "文档ID#编号"；确实不需要的章节给空数组）：

{"mapping": {"<node_id>": ["<文档ID>#<编号>"]}}"""

WIZARD_SECTION_SYSTEM_PROMPT = (
    "你是资深投标文件编写专家。撰写指定章节的正文，严格遵循本章摘要与图表计划，"
    "内容专业、具体、结构清晰、可直接用于投标文件。"
    "只输出 Markdown 正文：不要输出本节标题（系统会自动添加），不要输出解释或前言，"
    "不要把整节内容包进代码块围栏（mermaid 图表除外）。"
    "公司资质、业绩、项目、人员与数据必须来自「素材摘录」，不得虚构；"
    "素材与需求都未覆盖的事实性内容用“（请补充）”占位，由投标人自行补齐。"
    "图表要求："
    "①结构化内容（对比、参数、人员配置、职责分工、进度安排）优先用 Markdown 表格；"
    "②「本章图表计划」指定了 mermaid 图的，按计划的类型与标题输出 ```mermaid 代码块，"
    "语法必须严格合法，节点与标签使用中文，整段代码块独占成块、前后留空行；"
    "③图表涉及的名称、日期、数值必须与正文和素材一致，严禁虚构；"
    "④纯论述性内容保持文本段落，不要为凑图表而强行图示化。"
)

WIZARD_SECTION_USER_TEMPLATE = """## 项目招标要素（摘要）
__ANALYSIS_JSON__

## 编写需求（用户确认）
__REQUIREMENTS_TEXT__

## 投标文件大纲（整体结构）
__OUTLINE_TEXT__

## 本次撰写章节
标题：__TITLE__
本章摘要：__SUMMARY__
本章图表计划：
__CHART_PLAN_TEXT__
篇幅：约 __ARTICLE_COUNT__ 段，每段约 __TEXT_COUNT__ 字。

## 素材摘录（公司真实资料，事实性内容只能出自这里）
__MATERIAL_CHUNKS__

## 已完成章节（保持前后呼应，不要重复）
__PREV_SECTIONS__

请撰写该章节正文（Markdown，不含本节标题）。"""


class BidWizardCancelled(RuntimeError):
    """Raised when the user cancels a running wizard writing task."""


# ------------------------------------------------------------------ pure helpers


def estimate_tokens(chars: int) -> int:
    """Rough token estimate for the pre-upload confirmation dialog (量级提示)."""
    return max(1, int((chars or 0) * ESTIMATE_TOKENS_PER_CHAR))


def current_llm_for_pricing() -> tuple[str, Optional[str]]:
    """(provider, model) mirroring llm_factory's selection, for pre-billing estimates."""
    from backend.config import get_settings

    settings = get_settings()
    provider = (settings.llm_provider or "minimax").strip().lower()
    model = {
        "volcengine": settings.volcengine_model,
        "deepseek": settings.deepseek_model,
        "tencent": settings.tencent_model,
    }.get(provider, settings.mini_agent_model)
    return provider, model


def estimate_index_points(estimated_tokens: int, multiplier: Decimal | float) -> Optional[int]:
    """token→点数线性预估（上传前确认弹窗显示「约 X 点」，§4.2/story 7）。

    口径：索引=1 次 LLM 调用/素材——prompt≈全文 tokens，completion≈元数据输出，
    按 prompt 的 8%、下限 200 tokens 粗估；点数 = cost×10×倍率（与结算同公式），
    **向上取整**（预估略保守，小素材也至少显示 1 点）。
    价目缺失（provider 无费率）返回 None，前端退回 token 量级提示。
    """
    from backend.services.billing import sales_points_for
    from backend.services.cost_calculator import estimate_cost

    provider, model = current_llm_for_pricing()
    completion = max(200, int((estimated_tokens or 0) * 0.08))
    cost = estimate_cost(
        provider=provider,
        model=model,
        prompt_tokens=max(1, int(estimated_tokens or 0)),
        completion_tokens=completion,
        status="success",
    )
    if cost is None:
        return None
    points = sales_points_for(cost, multiplier)
    if points <= 0:
        return None
    return int(points.to_integral_value(rounding=ROUND_CEILING))


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def split_material_chunks(
    markdown: str, *, max_chars: int = MATERIAL_CHUNK_MAX_CHARS
) -> list[dict[str, Any]]:
    """Deterministic heading-aware split; LLM only writes per-chunk metadata.

    解析产物是 markdown：按标题分块、按段落打包到 max_chars 附近；单段超长硬切。
    分段文本由代码确定性生成（可回溯原文），LLM 只负责摘要/关键词（见 INDEX_META_*）。
    每段携带 location（标题路径，§5.3 frontmatter 的「原文定位」）。
    """
    units: list[tuple[str, str, str]] = []  # (heading, location, paragraph)
    stack: list[tuple[int, str]] = []  # (heading level, heading) 栈 → 标题路径
    heading = ""
    location = ""
    buffer: list[str] = []
    for raw_line in (markdown or "").splitlines():
        match = _HEADING_RE.match(raw_line.strip())
        if match:
            if buffer:
                units.append((heading, location, "\n".join(buffer).strip()))
                buffer = []
            level = len(match.group(1))
            heading = match.group(2).strip()[:200]
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            location = " > ".join(text for _, text in stack)[:300]
            continue
        buffer.append(raw_line)
    if buffer:
        units.append((heading, location, "\n".join(buffer).strip()))

    chunks: list[dict[str, Any]] = []

    def _emit(heading: str, location: str, text: str) -> None:
        text = text.strip()
        if text and len(chunks) < MATERIAL_MAX_CHUNKS:
            chunks.append(
                {
                    "no": len(chunks) + 1,
                    "heading": heading,
                    "location": location or f"第{len(chunks) + 1}段",
                    "text": text,
                }
            )

    for heading, location, paragraph in units:
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            _emit(heading, location, paragraph)
            continue
        # oversized paragraph: hard split on sentence boundaries
        start = 0
        while start < len(paragraph) and len(chunks) < MATERIAL_MAX_CHUNKS:
            window = paragraph[start : start + max_chars]
            cut = max(window.rfind("。"), window.rfind("；"), window.rfind("\n"))
            if cut < max_chars // 2:
                cut = max_chars
            _emit(heading, location, paragraph[start : start + cut])
            start += cut
    return chunks


def render_chunk_file(chunk: dict[str, Any], meta: dict[str, Any]) -> str:
    keywords = "、".join(str(item) for item in (meta.get("keywords") or [])[:5])
    header = (
        f"<!-- bid-wizard-material\n"
        f"no: {chunk['no']}\n"
        f"heading: {str(chunk.get('heading') or '')[:200]}\n"
        f"location: {str(chunk.get('location') or '')[:300]}\n"
        f"summary: {str(meta.get('summary') or '')[:200]}\n"
        f"keywords: {keywords}\n"
        f"-->\n"
    )
    return header + chunk["text"]


def render_index_markdown(chunks: list[dict[str, Any]], metas: dict[int, dict[str, Any]]) -> str:
    lines = ["| 编号 | 标题 | 摘要 | 关键词 |", "| --- | --- | --- | --- |"]
    for chunk in chunks:
        meta = metas.get(int(chunk["no"]), {})
        keywords = "、".join(str(item) for item in (meta.get("keywords") or [])[:5])
        lines.append(
            f"| {chunk['no']} | {chunk.get('heading') or ''} | "
            f"{str(meta.get('summary') or '')[:120]} | {keywords} |"
        )
    return "\n".join(lines)


def normalize_questionnaire(payload: Any) -> dict[str, Any]:
    """Bound and id-stamp the questionnaire JSON produced by the LLM."""
    raw_questions = []
    if isinstance(payload, dict):
        raw_questions = payload.get("questions") or []
    elif isinstance(payload, list):
        raw_questions = payload
    questions: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_questions[:QUESTIONNAIRE_MAX_QUESTIONS]):
        if not isinstance(raw, dict):
            continue
        question = str(raw.get("question") or "").strip()[:500]
        if not question:
            continue
        questions.append(
            {
                "id": str(raw.get("id") or f"q{len(questions) + 1}")[:100],
                "topic": str(raw.get("topic") or "其他").strip()[:50],
                "question": question,
                "why": str(raw.get("why") or "").strip()[:500],
                "suggested_answer": str(raw.get("suggested_answer") or "").strip()[:2_000],
                "source": str(raw.get("source") or "").strip()[:200],
                "inferred": bool(raw.get("inferred")),
                # 作答区（用户提交后回填）
                "action": None,
                "answer": None,
                "effective_answer": None,
            }
        )
    return {"questions": questions}


def merge_questionnaire_answers(
    questionnaire: dict[str, Any] | None, answers: list[dict[str, Any]]
) -> dict[str, Any]:
    """Merge user answers into a stored questionnaire → 编写需求 JSON (pure).

    effective_answer：answered 取用户输入；adopted 取建议答案；skipped 置空
    （skipped 的问题在下游 prompt 中整条省略，不进入编写需求）。
    """
    questions = list((questionnaire or {}).get("questions") or [])
    by_id = {str(item.get("id")): item for item in questions if isinstance(item, dict)}
    for answer in answers or []:
        question = by_id.get(str(answer.get("question_id")))
        if question is None:
            continue
        action = answer.get("action")
        if action not in ("answered", "adopted", "skipped"):
            action = "skipped"
        user_answer = str(answer.get("answer") or "").strip()[:4_000] or None
        question["action"] = action
        question["answer"] = user_answer
        if action == "answered":
            question["effective_answer"] = user_answer
        elif action == "adopted":
            question["effective_answer"] = question.get("suggested_answer")
        else:
            question["effective_answer"] = None
    return {"questions": questions}


def build_requirements_text(requirements: dict[str, Any] | None) -> str:
    """Render 编写需求 as readable text for spec/section prompts."""
    lines: list[str] = []
    for question in (requirements or {}).get("questions") or []:
        if not isinstance(question, dict):
            continue
        effective = str(question.get("effective_answer") or "").strip()
        if not effective:
            continue  # skipped / 未回答的不进入下游
        topic = str(question.get("topic") or "其他")
        label = str(question.get("question") or "")[:200]
        flag = "（AI 推断，请确认）" if question.get("inferred") and question.get("action") == "adopted" else ""
        lines.append(f"- [{topic}] {label}：{effective[:500]}{flag}")
    # 补充说明（决策 32：追问侧栏采纳对 + AI 主动反问的补答），与问卷答案同权重进上下文
    for item in (requirements or {}).get("supplementals") or []:
        if not isinstance(item, dict):
            continue
        supplemental_q = str(item.get("question") or "").strip()
        supplemental_a = str(item.get("answer") or "").strip()
        if supplemental_q and supplemental_a:
            lines.append(f"- [补充说明] {supplemental_q[:200]}：{supplemental_a[:500]}")
    if not lines:
        return "无"
    return "\n".join(lines)[:SPEC_CONTEXT_MAX_CHARS]


def _bound_charts(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list):
        return None
    charts: list[dict[str, Any]] = []
    for item in raw[:6]:
        if not isinstance(item, dict):
            continue
        chart_type = str(item.get("type") or "table").strip().lower()
        if chart_type not in ("table", "mermaid"):
            chart_type = "table"
        title = str(item.get("title") or "").strip()[:200]
        if not title:
            continue
        charts.append(
            {"type": chart_type, "title": title, "points": str(item.get("points") or "").strip()[:1_000] or None}
        )
    return charts or None


_NUMERIC_TITLE_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)*(?:[\s、:：\-—]+|\.\s+)")


def _strip_numeric_title_prefix(title: str) -> str:
    stripped = _NUMERIC_TITLE_PREFIX_RE.sub("", title or "").strip()
    return stripped or title


def _smooth_levels(nodes: list[Any]) -> list[dict[str, Any]]:
    """层级平滑：首节点强制 level=1、后续不跳级（防 "0.1"/"1.0.1" 这类编号）。

    V1 normalize_outline 对"首个节点 level>1 / 中途跳级"会产出前导 0 编号；
    向导的 Spec 来源含用户手工编辑，必须在入口先平滑（仅新模块行为，不动 V1）。
    """
    smoothed: list[dict[str, Any]] = []
    prev_level = 0
    for raw in nodes or []:
        if not isinstance(raw, dict):
            continue
        try:
            level = max(1, min(6, int(raw.get("level") or 1)))
        except (TypeError, ValueError):
            level = 1
        if not smoothed:
            level = 1
        else:
            level = min(level, prev_level + 1)
        smoothed.append({**raw, "level": level})
        prev_level = level
    return smoothed


def normalize_spec(nodes: list[Any]) -> list[dict[str, Any]]:
    """Normalize a spec (V1 outline superset): V1 numbering kernel + summary/charts."""
    nodes = _smooth_levels(nodes)
    base = normalize_outline(nodes)  # node_id/title/level/requirement/article_count/text_count
    by_title: dict[str, dict[str, Any]] = {}
    for raw in nodes:
        if isinstance(raw, dict):
            title = str(raw.get("title") or "").strip()[:200]
            if title:
                # normalize_outline 会剥标题的数字前缀（"1.1 x"→"x"），两个键都登记
                by_title[title] = raw
                by_title.setdefault(_strip_numeric_title_prefix(title), raw)
    spec: list[dict[str, Any]] = []
    for node in base:
        raw = by_title.get(node["title"], {})
        summary = str(raw.get("summary") or "").strip()[:2_000] or None
        charts = _bound_charts(raw.get("charts"))
        spec.append(
            {
                "node_id": node["node_id"],
                "title": node["title"],
                "level": node["level"],
                "summary": summary,
                "article_count": node["article_count"],
                "text_count": node["text_count"],
                "charts": charts,
            }
        )
    return spec


def build_chart_plan_text(node: dict[str, Any]) -> str:
    charts = node.get("charts") or []
    if not charts:
        return "- 无（本章按内容需要自行把握，结构化内容优先表格）"
    lines = []
    for chart in charts:
        chart_type = "表格" if chart.get("type") == "table" else "mermaid 图"
        points = f"：{chart['points']}" if chart.get("points") else ""
        lines.append(f"- [{chart_type}] {chart.get('title')}{points}")
    return "\n".join(lines)


def _outline_text(spec: list[dict[str, Any]]) -> str:
    lines = []
    for node in spec:
        indent = "  " * (max(1, int(node.get("level") or 1)) - 1)
        lines.append(f"{indent}- {node.get('title')}")
    return "\n".join(lines)[:OUTLINE_CONTEXT_MAX_CHARS]


def parse_chunk_refs(payload: Any, valid_doc_ids: set[str]) -> list[str]:
    """Validate "docId#no" refs from the selection LLM call."""
    refs: list[str] = []
    if isinstance(payload, dict):
        refs = [str(item) for item in (payload.get("chunks") or [])]
    elif isinstance(payload, list):
        refs = [str(item) for item in payload]
    result: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if "#" not in ref:
            continue
        doc_id, _, no = ref.partition("#")
        doc_id, no = doc_id.strip(), no.strip()
        if not doc_id or not no or doc_id not in valid_doc_ids:
            continue
        if not re.fullmatch(r"\d{1,3}", no):
            continue
        key = f"{doc_id}#{int(no)}"
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result[:60]


def parse_chunk_ref_mapping(
    payload: Any, valid_doc_ids: set[str], node_ids: list[str]
) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    raw = payload.get("mapping") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return mapping
    for node_id, refs in raw.items():
        key = str(node_id).strip()
        if key not in mapping or not isinstance(refs, list):
            continue
        mapping[key] = parse_chunk_refs({"chunks": refs}, valid_doc_ids)
    return mapping


# ------------------------------------------------------------------ llm helpers

WIZARD_ANALYSIS_EXTRA_INSTRUCTION = (
    "\n\n另外，请在输出的 JSON 对象中增加 suggested_materials 字段："
    '[{"name": "建议投标人补充上传的素材名称", "reason": "对应哪条招标要求/评分标准"}]，'
    "最多 8 项，聚焦招标文件明确要求、但投标公司通常需要另行准备原件的证明材料"
    "（如类似业绩合同、人员证书、厂家授权函、检测报告等）。"
)


def _bound_suggested_materials(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, str]] = []
    for entry in raw:
        if len(items) >= 8:
            break
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()[:100]
        if not name:
            continue
        items.append({"name": name, "reason": str(entry.get("reason") or "").strip()[:200]})
    return items


class WizardLLM:
    """Thin instrumented client wrapper shared by sync and celery flows."""

    def __init__(self, *, timeout: float = 180.0) -> None:
        self.client = instrument_llm_client(create_llm_client(timeout=timeout))

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        from mini_agent.schema import Message

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        response = await self.client.generate(messages=messages)
        return str(getattr(response, "content", "") or "").strip()

    async def generate_json(self, system_prompt: str, user_prompt: str) -> Any:
        parsed = parse_json_payload(await self.generate(system_prompt, user_prompt))
        if parsed is None:
            raise RuntimeError("模型输出不是合法 JSON，请重试")
        return parsed


async def analyze_tender(llm: WizardLLM, tender_markdown: str) -> dict[str, Any]:
    """招标要素提取（复用 V1 tender_analysis 的 prompt 与口径）。

    向导侧在 user prompt 末尾追加 instructed 字段 suggested_materials
    （§4.2：AI 解读招标文件后建议补充哪些素材），V1 内核 prompt 保持零改动；
    _bound_analysis 是字段白名单，该字段从原始输出单独提取归一。
    """
    tender_text = (tender_markdown or "")[:ANALYSIS_CONTEXT_MAX_CHARS]
    if len(tender_markdown or "") > ANALYSIS_CONTEXT_MAX_CHARS:
        tender_text += "\n…（后文已截断）"
    user_prompt = (ANALYSIS_USER_TEMPLATE + WIZARD_ANALYSIS_EXTRA_INSTRUCTION).replace(
        "__TENDER_TEXT__", tender_text
    )
    raw = await llm.generate_json(ANALYSIS_SYSTEM_PROMPT, user_prompt)
    if not isinstance(raw, dict):
        raise RuntimeError("招标要素提取结果不是 JSON 对象")
    analysis = _bound_analysis(raw)
    analysis["suggested_materials"] = _bound_suggested_materials(raw.get("suggested_materials"))
    return analysis


async def generate_questionnaire(
    llm: WizardLLM, *, analysis: dict[str, Any], material_index_text: str
) -> dict[str, Any]:
    user_prompt = (
        QUESTIONNAIRE_USER_TEMPLATE.replace(
            "__ANALYSIS_JSON__", json.dumps(analysis, ensure_ascii=False)[:ANALYSIS_JSON_MAX_CHARS]
        ).replace("__MATERIAL_INDEX__", material_index_text or "（无素材）")
    )
    payload = await llm.generate_json(QUESTIONNAIRE_SYSTEM_PROMPT, user_prompt)
    questionnaire = normalize_questionnaire(payload)
    if not questionnaire["questions"]:
        raise RuntimeError("问卷生成结果为空，请重试")
    return questionnaire


async def generate_spec(
    llm: WizardLLM,
    *,
    analysis: dict[str, Any],
    requirements_text: str,
    material_index_text: str,
) -> list[dict[str, Any]]:
    user_prompt = (
        SPEC_USER_TEMPLATE.replace(
            "__ANALYSIS_JSON__", json.dumps(analysis, ensure_ascii=False)[:ANALYSIS_JSON_MAX_CHARS]
        )
        .replace("__REQUIREMENTS_TEXT__", (requirements_text or "无")[:SPEC_CONTEXT_MAX_CHARS])
        .replace("__MATERIAL_INDEX__", material_index_text or "（无素材）")
        .replace("__MAX_NODES__", str(OUTLINE_MAX_NODES))
    )
    payload = await llm.generate_json(SPEC_SYSTEM_PROMPT, user_prompt)
    nodes = payload if isinstance(payload, list) else payload.get("outline") if isinstance(payload, dict) else None
    spec = normalize_spec(nodes if isinstance(nodes, list) else [])
    if not spec:
        raise RuntimeError("Spec 生成结果为空，请重试")
    return spec


async def revise_spec(
    llm: WizardLLM, *, current_spec: list[dict[str, Any]], instruction: str
) -> list[dict[str, Any]]:
    user_prompt = REVISE_USER_TEMPLATE.replace(
        "__CURRENT_SPEC__", json.dumps(current_spec, ensure_ascii=False)[:SPEC_CONTEXT_MAX_CHARS]
    ).replace("__INSTRUCTION__", (instruction or "").strip()[:2_000])
    payload = await llm.generate_json(REVISE_SYSTEM_PROMPT, user_prompt)
    nodes = payload if isinstance(payload, list) else payload.get("outline") if isinstance(payload, dict) else None
    spec = normalize_spec(nodes if isinstance(nodes, list) else [])
    if not spec:
        raise RuntimeError("Spec 修订结果为空，请重试")
    return spec


async def answer_sidebar_question(
    llm: WizardLLM,
    *,
    question: str,
    analysis: dict[str, Any],
    requirements_text: str,
    material_index_text: str,
) -> str:
    """追问侧栏（决策 32a）：基于招标要素+已确认需求+素材索引回答用户自由提问。"""
    system_prompt = (
        "你是资深投标编写顾问。请基于给定的招标要素、已确认的编写需求与公司素材索引，"
        "回答用户关于本次投标的提问。优先引用素材索引中的真实内容（可注明出处素材名），"
        "素材与需求未覆盖的部分如实说明并给出建议写法。回答用中文，500 字以内，不要编造事实。"
    )
    user_prompt = (
        f"招标要素：\n{json.dumps(analysis or {}, ensure_ascii=False)[:ANALYSIS_JSON_MAX_CHARS]}\n\n"
        f"已确认的编写需求：\n{(requirements_text or '无')[:SPEC_CONTEXT_MAX_CHARS]}\n\n"
        f"素材索引：\n{material_index_text or '（无素材）'}\n\n"
        f"用户提问：{(question or '').strip()[:2_000]}"
    )
    answer = await llm.generate(system_prompt, user_prompt)
    if not answer:
        raise RuntimeError("AI 未能回答该问题，请换个问法重试")
    return answer[:4_000]


async def generate_followup_questions(
    llm: WizardLLM,
    *,
    analysis: dict[str, Any],
    requirements_text: str,
    material_index_text: str,
) -> list[dict[str, Any]]:
    """AI 主动反问（决策 32b/Q19）：对照招标要素检测已保存需求的关键缺口，≤3 条、单轮。"""
    system_prompt = (
        "你是投标需求评审专家。对照招标文件要素与用户已确认的编写需求，找出最多 3 个"
        "会显著影响标书质量的关键信息缺口（如资质、业绩、人员、实施边界、服务承诺等）。"
        '只输出 JSON：{"followups": [{"question": "...", "why": "..."}]}。'
        "question 是向用户追问的具体问题（一句话、可直接回答）；why 一句话说明为什么关键。"
        "没有实质缺口时输出空数组。不要重复用户已回答或素材已覆盖的信息。"
    )
    user_prompt = (
        f"招标要素：\n{json.dumps(analysis or {}, ensure_ascii=False)[:ANALYSIS_JSON_MAX_CHARS]}\n\n"
        f"已确认的编写需求：\n{(requirements_text or '无')[:SPEC_CONTEXT_MAX_CHARS]}\n\n"
        f"素材索引：\n{material_index_text or '（无素材）'}"
    )
    payload = await llm.generate_json(system_prompt, user_prompt)
    raw = payload.get("followups") if isinstance(payload, dict) else payload
    followups: list[dict[str, Any]] = []
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()[:500]
        if not question:
            continue
        followups.append(
            {"id": f"fu{index + 1}", "question": question, "why": str(item.get("why") or "").strip()[:500]}
        )
    return followups[:3]


# ------------------------------------------------------- material index io


def material_dir(workspace_path: Path, wizard_id: str, document_id: str) -> Path:
    return Path(workspace_path) / "bid-wizard" / wizard_id / "materials" / document_id


def write_material_index(
    workspace_path: Path, wizard_id: str, document_id: str, chunks: list[dict[str, Any]], metas: dict[int, dict[str, Any]]
) -> None:
    base = material_dir(workspace_path, wizard_id, document_id)
    chunk_dir = base / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for chunk in chunks:
        path = chunk_dir / f"{int(chunk['no']):03d}.md"
        path.write_text(render_chunk_file(chunk, metas.get(int(chunk["no"]), {})), encoding="utf-8")
    (base / "index.md").write_text(render_index_markdown(chunks, metas), encoding="utf-8")


async def _load_material_index_rows(db, wizard_id: str) -> list[tuple[str, str, str]]:
    rows = (
        await db.execute(
            select(BidWizardMaterial, Document)
            .join(Document, Document.id == BidWizardMaterial.document_id)
            .where(
                BidWizardMaterial.wizard_id == wizard_id,
                BidWizardMaterial.index_status == "indexed",
            )
        )
    ).all()
    from backend.config import get_settings

    entries: list[tuple[str, str, str]] = []
    for material, document in rows:
        index_path = material_dir(get_settings().workspace_path, wizard_id, document.id) / "index.md"
        try:
            entries.append(
                (
                    document.id,
                    document.original_filename or "",
                    index_path.read_text(encoding="utf-8", errors="replace"),
                )
            )
        except Exception:
            logger.exception("bid-wizard index read failed: wizard=%s doc=%s", wizard_id, document.id)
    return entries


async def load_material_index_texts(
    session_factory, wizard_id: str
) -> list[tuple[str, str, str]]:
    """Read index.md of every indexed material: [(document_id, filename, index_md)]."""
    async with session_factory() as db:
        return await _load_material_index_rows(db, wizard_id)


def build_material_index_text(entries: list[tuple[str, str, str]]) -> str:
    blocks = [f"【素材 {doc_id}｜{name}】\n{index_md}" for doc_id, name, index_md in entries]
    return "\n\n".join(blocks)[:SPEC_CONTEXT_MAX_CHARS]


def read_material_chunks_text(workspace_path: Path, wizard_id: str, refs: list[str]) -> str:
    """Second phase of the two-step injection: read selected chunk files."""
    parts: list[str] = []
    total = 0
    for ref in refs:
        doc_id, _, no = ref.partition("#")
        path = material_dir(workspace_path, wizard_id, doc_id) / "chunks" / f"{int(no):03d}.md"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # strip the metadata header comment for prompt use
        if text.startswith("<!--"):
            end = text.find("-->")
            if end >= 0:
                text = text[end + 3 :].strip()
        parts.append(f"〔素材 {doc_id}#{no}〕\n{text.strip()}")
        total += len(text)
        if total >= MATERIAL_CONTEXT_MAX_CHARS:
            break
    return "\n\n".join(parts) if parts else "（无可用素材摘录）"


async def select_chunks_for_sections(
    llm: WizardLLM,
    *,
    material_entries: list[tuple[str, str, str]],
    spec_scope: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """One mapped two-phase retrieval call per writing task (bounded cost)."""
    node_ids = [node["node_id"] for node in spec_scope]
    if not material_entries:
        return {node_id: [] for node_id in node_ids}
    index_text = build_material_index_text(material_entries)
    node_list = "\n".join(
        f"{node['node_id']}: {node['title']} — {str(node.get('summary') or '')[:120]}" for node in spec_scope
    )
    user_prompt = SELECT_MAPPED_USER_TEMPLATE.replace("__MATERIAL_INDEX__", index_text).replace(
        "__NODE_LIST__", node_list
    )
    valid_doc_ids = {doc_id for doc_id, _, _ in material_entries}
    try:
        payload = await llm.generate_json(SELECT_SYSTEM_PROMPT, user_prompt)
    except Exception:
        logger.exception("bid-wizard chunk selection failed; continuing without materials")
        return {node_id: [] for node_id in node_ids}
    return parse_chunk_ref_mapping(payload, valid_doc_ids, node_ids)


# ------------------------------------------------------------------ writing


class BidWizardWriteAgent:
    """Orchestrates one writing task: per-section generation with wizard context."""

    def __init__(
        self,
        *,
        task_id: str,
        workspace_dir: Path,
        session_factory,
        event_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
        cancel_event: Optional["asyncio.Event"] = None,
        llm_timeout: float = 180.0,
    ) -> None:
        self.task_id = task_id
        self.workspace_dir = Path(workspace_dir)
        self.session_factory = session_factory
        self.event_callback = event_callback
        self.cancel_event = cancel_event
        self.llm = WizardLLM(timeout=llm_timeout)

    def _publish(self, event_type: str, data: dict[str, Any]) -> None:
        if self.event_callback is not None:
            try:
                self.event_callback(event_type, data or {})
            except Exception:
                logger.exception("bid-wizard event publish failed: task=%s", self.task_id)

    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise BidWizardCancelled("用户取消了撰写任务")

    async def _load_context(self) -> dict[str, Any]:
        async with self.session_factory() as db:
            task = (
                await db.execute(select(BidWritingTask).where(BidWritingTask.id == self.task_id))
            ).scalar_one()
            from backend.models import BidWizard

            wizard = (
                await db.execute(select(BidWizard).where(BidWizard.id == task.wizard_id))
            ).scalar_one()
        return {
            "task": task,
            "wizard": wizard,
            "selected": set(task.selected_nodes or []),
            "spec": wizard.spec if isinstance(wizard.spec, list) else [],
            "analysis": wizard.analysis if isinstance(wizard.analysis, dict) else {},
            "requirements": wizard.requirements if isinstance(wizard.requirements, dict) else None,
        }

    async def _ensure_section_rows(self, spec_scope: list[dict[str, Any]]) -> None:
        async with self.session_factory() as db:
            existing = set(
                (
                    await db.execute(
                        select(BidWizardSection.node_id).where(BidWizardSection.task_id == self.task_id)
                    )
                ).scalars()
            )
            for node in spec_scope:
                if node["node_id"] not in existing:
                    db.add(
                        BidWizardSection(
                            task_id=self.task_id,
                            node_id=node["node_id"][:200],
                            title=node["title"][:500],
                            summary=str(node.get("summary") or "")[:2_000] or None,
                            chart_plan=node.get("charts"),
                            status="pending",
                        )
                    )
            await db.commit()

    async def _set_section_status(self, node_id: str, **fields: Any) -> None:
        async with self.session_factory() as db:
            row = (
                await db.execute(
                    select(BidWizardSection).where(
                        BidWizardSection.task_id == self.task_id,
                        BidWizardSection.node_id == node_id,
                    )
                )
            ).scalar_one()
            for key, value in fields.items():
                setattr(row, key, value)
            await db.commit()

    async def _load_prev_sections(
        self, wizard_id: str, spec: list[dict[str, Any]]
    ) -> list[tuple[str, str, Optional[str]]]:
        """跨任务 run 的已生成/已写入章节（§5.3：标题+摘要，保持前后章呼应、支撑断点续作）。

        title/summary 建行时拷贝自 spec（撰写中 spec 锁定，跨 run 稳定），
        按大纲顺序输出；同一 node 多个 run 的行取任一版本即可。
        """
        async with self.session_factory() as db:
            rows = (
                await db.execute(
                    select(BidWizardSection.node_id, BidWizardSection.title, BidWizardSection.summary)
                    .join(BidWritingTask, BidWritingTask.id == BidWizardSection.task_id)
                    .where(
                        BidWritingTask.wizard_id == wizard_id,
                        BidWizardSection.status.in_(("written", "generated")),
                    )
                )
            ).all()
        by_node = {str(node_id): (str(title or ""), summary) for node_id, title, summary in rows}
        ordered: list[tuple[str, str, Optional[str]]] = []
        for node in spec:
            entry = by_node.get(str(node.get("node_id")))
            if entry is not None:
                ordered.append(
                    (str(node.get("node_id")), entry[0] or str(node.get("title") or ""), entry[1])
                )
        return ordered

    async def _generate_section(
        self,
        node: dict[str, Any],
        *,
        analysis: dict[str, Any],
        requirements_text: str,
        spec: list[dict[str, Any]],
        material_refs: list[str],
        prev_sections: list[tuple[str, str, Optional[str]]],
    ) -> dict[str, Any]:
        self._publish("section_started", {"node_id": node["node_id"], "title": node["title"]})
        await self._set_section_status(node["node_id"], status="generating")
        from backend.config import get_settings

        material_chunks = read_material_chunks_text(
            get_settings().workspace_path, self._wizard_id, material_refs
        )
        prev_items = [item for item in prev_sections if item[0] != node["node_id"]][-12:]
        prev_text = (
            "\n".join(
                f"- {node_id} {title}：{str(summary or '').strip()[:80]}"
                if str(summary or "").strip()
                else f"- {node_id} {title}"
                for node_id, title, summary in prev_items
            )
            or "（本章节之前没有已完成的章节）"
        )
        analysis_json = json.dumps(analysis, ensure_ascii=False)[:ANALYSIS_JSON_MAX_CHARS]
        user_prompt = (
            WIZARD_SECTION_USER_TEMPLATE.replace("__ANALYSIS_JSON__", analysis_json)
            .replace("__REQUIREMENTS_TEXT__", requirements_text or "无")
            .replace("__OUTLINE_TEXT__", _outline_text(spec))
            .replace("__TITLE__", node["title"])
            .replace("__SUMMARY__", str(node.get("summary") or "（无摘要，按标题与大纲撰写）"))
            .replace("__CHART_PLAN_TEXT__", build_chart_plan_text(node))
            .replace("__MATERIAL_CHUNKS__", material_chunks)
            .replace("__PREV_SECTIONS__", prev_text)
            .replace("__ARTICLE_COUNT__", str(node.get("article_count") or 2))
            .replace("__TEXT_COUNT__", str(node.get("text_count") or 400))
        )
        body = strip_code_fence(await self.llm.generate(WIZARD_SECTION_SYSTEM_PROMPT, user_prompt))
        if not body:
            raise RuntimeError(f"章节「{node['title']}」生成结果为空")
        body = clamp_mermaid_blocks(close_unterminated_mermaid_fence(body))
        level = max(1, min(6, int(node.get("level") or 1)))
        content = f"{'#' * level} {node['title']}\n\n{body}"[:SECTION_RESULT_MAX_CHARS]

        section_path = self.workspace_dir / "sections" / f"{_safe_filename(node['node_id'])}.md"
        section_path.parent.mkdir(parents=True, exist_ok=True)
        section_path.write_text(content, encoding="utf-8")
        word_count = len(re.sub(r"\s", "", content))
        await self._set_section_status(
            node["node_id"],
            status="generated",
            content_path=str(section_path),
            word_count=word_count,
            error_message=None,
        )
        self._publish(
            "section_completed",
            {"node_id": node["node_id"], "title": node["title"], "word_count": word_count},
        )
        return {"node_id": node["node_id"], "title": node["title"], "word_count": word_count}

    async def _mark_section_failed(self, node_id: str, message: str) -> None:
        try:
            await self._set_section_status(node_id, status="failed", error_message=message[:2_000])
        except Exception:
            logger.exception("bid-wizard section failure mark failed: task=%s node=%s", self.task_id, node_id)

    async def run(self) -> dict[str, Any]:
        context = await self._load_context()
        task = context["task"]
        wizard = context["wizard"]
        self._wizard_id = wizard.id
        selected = context["selected"]
        spec = context["spec"]
        spec_scope = [node for node in spec if not selected or node["node_id"] in selected]
        if not spec_scope:
            raise RuntimeError("没有需要撰写的章节")

        await self._ensure_section_rows(spec_scope)
        self._publish("phase", {"phase": "generating", "section_total": len(spec_scope)})

        material_entries = await load_material_index_texts(self.session_factory, wizard.id)
        ref_mapping = await select_chunks_for_sections(
            self.llm, material_entries=material_entries, spec_scope=spec_scope
        )
        requirements_text = build_requirements_text(context["requirements"])
        prev_sections = await self._load_prev_sections(wizard.id, spec)

        done: list[dict[str, Any]] = []
        failed: list[str] = []
        for node in spec_scope:
            self._check_cancel()
            try:
                result = await self._generate_section(
                    node,
                    analysis=context["analysis"],
                    requirements_text=requirements_text,
                    spec=spec,
                    material_refs=ref_mapping.get(node["node_id"], []),
                    prev_sections=prev_sections,
                )
                done.append(result)
                prev_sections.append((node["node_id"], node["title"], node.get("summary")))
            except BidWizardCancelled:
                raise
            except Exception as exc:
                message = str(exc)[:500]
                await self._mark_section_failed(node["node_id"], message)
                failed.append(node["node_id"])
                self._publish("section_failed", {"node_id": node["node_id"], "error": message})

        self._check_cancel()
        summary = {
            "section_total": len(spec_scope),
            "section_generated": len(done),
            "section_failed": len(failed),
            "word_count": sum(int(item.get("word_count") or 0) for item in done),
        }
        return summary


def _safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.\-]", "_", value) or "section"
