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
import os
import re
import time
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
    SECTION_SYSTEM_PROMPT_BIDFIG,
    _bound_analysis,
    clamp_mermaid_blocks,
    close_unterminated_mermaid_fence,
    extract_mermaid_fences,
    normalize_outline,
    parse_json_payload,
    strip_code_fence,
)
from backend.agent.bid_doc_checks import (
    chart_placeholder_issues,
    draft_checks,
    renumber_captions,
    stance_conflict_targets,
    strip_leading_title_headings,
)
from backend.agent.bidfig import (
    INVALID_PLACEHOLDER,
    bidfig_fence_payloads,
    clamp_bidfig_blocks,
    close_unterminated_bidfig_fence,
    extract_bidfig_fences,
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

# ---- 生成要求（决策 38-41，2026-09-14）：编写需求里固定的三项硬约束子结构 ----
GENERATION_PARTS: tuple[str, ...] = ("business", "technical")
GENERATION_PART_LABELS = {"business": "商务部分", "technical": "技术部分"}
# 2026-10-23 产品要求：屏蔽商务应标编写，只提供技术应标部分——parts 消费侧只放行 technical
GENERATION_PARTS_ENABLED: tuple[str, ...] = ("technical",)
# 图表引擎（23 号 O8）：bidfig=原语库确定性渲染。2026-10-24 用户反馈废弃纯 mermaid 制图——
# 存量 mermaid 选项在规范化/读取时一律归一为 bidfig（mermaid 分支仅作 charts=false 的纯文态）
GENERATION_FIG_MODES: tuple[str, ...] = ("bidfig",)
# 只勾一个部分时，告诉 Spec 生成器另一部分的典型章节不得出现
_GENERATION_PART_EXCLUSIONS = {
    "technical": "商务部分的章节（如资质证明、业绩证明、报价、商务条款响应/偏离表）",
    "business": "技术部分的章节（如技术方案、实施方案、质量保障、售后服务）",
}
GENERATION_WORD_COUNT_MIN = 1_000
GENERATION_WORD_COUNT_MAX = 1_000_000
# Spec 生成按目标总字数 ±20% 软约束规划章数与每章字数（决策 40）
GENERATION_WORD_COUNT_TOLERANCE = 0.2
# 平级劣化判据（2026-09-15 预发布实证：字数约束下模型把 56 章全标 level=1）：
# 全部节点 level=1 且数量超过此值即视为结构劣化，带纠正指令重试一次；少量一级章节的小纲要属正常
SPEC_FLAT_DEGRADATION_MIN_NODES = 12
# 重试只在时间预算允许时进行：首轮耗时 × 该系数 < 总预算（重试耗时按与首轮同量级估），
# 否则宁可返回平级结果，也不能让整个同步微任务超时把首轮结果一起丢掉
_SPEC_RETRY_BUDGET_FACTOR = 2.2

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
    "收集编写技术应标书还缺的必要补充知识（如实施方案约束、交付里程碑与验收口径、"
    "项目班子人员技术资历、培训与售后安排等）。"
    "本次投标编写仅面向技术应标部分（商务应标已关闭）：严禁围绕资质证书、业绩证明、"
    "财务报表、纳税社保证明、报价与商务条款等商务内容提问，也不要建议补充商务类材料。"
    "只输出一个合法 JSON 对象，不要输出任何其他文字或代码块标记。"
)

QUESTIONNAIRE_USER_TEMPLATE = """## 招标要素
__ANALYSIS_JSON__

## 公司素材索引（每行一段：编号｜标题｜摘要｜关键词）
__MATERIAL_INDEX__

## 检查范围（用户设定的生成内容，优先级高于下方通用规则）
__SCOPE_CONSTRAINTS__

请输出问卷 JSON：

{"questions": [
  {"topic": "主题（技术/人员/交付/其他）",
   "question": "向投标人提出的问题，一句话",
   "why": "为什么需要这个信息（与招标要求的关联）",
   "suggested_answer": "建议答案：优先采用素材索引中出现的真实事实，并注明来源；素材中没有则按常见做法推断",
   "source": "素材依据（如 '素材名#3'），推断时填 '推断'",
   "inferred": false}
]}

规则：
1. 共 6-15 题，按主题分组排序，只问「检查范围」内、编写标书真正必需、且招标文件与素材里都没有的信息；
2. 能从素材索引直接回答的不要问，直接写进 suggested_answer 并给 source；
3. 素材没有依据的建议答案必须 inferred=true，提示用户确认；
4. 严禁在答案里虚构公司资质、业绩、人员与数据。"""

SPEC_SYSTEM_PROMPT = (
    "你是资深投标文件编写专家。根据编写需求、招标要素与素材索引，设计多级目录结构的投标文件编写大纲（Spec）。"
    "只输出一个合法 JSON 数组，不要输出任何其他文字或代码块标记。"
)

SPEC_USER_TEMPLATE = """## 招标要素
__ANALYSIS_JSON__

## 编写需求（用户问卷确认）
__REQUIREMENTS_TEXT__

## 生成要求（用户设定的硬约束，优先级高于下方通用规则）
__GENERATION_CONSTRAINTS__

## 公司素材索引（每行一段：编号｜标题｜摘要｜关键词）
__MATERIAL_INDEX__
__TOC_REFERENCE__

请输出 Spec JSON 数组（不超过 __MAX_NODES__ 个节点；按目录顺序平铺，用 level 标注层级，1-4 级），每项格式：

{"title": "章节标题", "level": 1,
 "summary": "本章内容要点摘要（1-3 句，写作时的执行纲领）",
 "article_count": 2, "text_count": 400,
 "charts": [{"type": "table|mermaid", "title": "图表标题", "points": "图表要呈现的要点"}]}

层级示例（小节紧跟其所属大章之后；示例省略了其他字段）：
[{"title": "技术方案", "level": 1}, {"title": "总体架构设计", "level": 2}, {"title": "网络架构", "level": 3},
 {"title": "数据架构", "level": 3}, {"title": "关键技术选型", "level": 2}, {"title": "实施方案", "level": 1}]

规则：
1. 大纲完整覆盖招标需求与评分标准对应的响应内容，与评分办法呼应；只覆盖「生成要求」指定的生成内容范围；
2. 必须是多级目录：一级大章（level=1）一般 6-12 个，每个大章下按内容拆分二级小节（level=2），内容复杂的小节再拆三级（level=3）；level 必须如实标注所属层级，严禁把全部章节都标成 level=1 平铺——只有目标总字数不足 5,000 字时才允许仅有一级章节；
3. summary 写清楚本章要回应什么、用什么素材支撑；
4. charts 按「生成要求」执行：需要配图时按内容需要规划（每章 0-3 项）——对比/参数/人员/进度用 table，组织架构/流程/横道图/占比用 mermaid；图（mermaid 类）只规划在叶子小节上，一级大章与含子节的父章节正文只是章首导语、不承载图（表格不限）；不配图时每章 charts 一律为 []；
5. 素材里有真实业绩/资质可引用的章节，在 summary 中点明引用方向；严禁虚构；
6. 「生成要求」给了目标总字数时，所有章节 article_count×text_count 之和必须落在允许区间——通过调整每章篇幅（article_count/text_count）与小节数量来满足，不得为凑字数把大纲压成只有一级章节的平级结构；
7. 招标要素含 scoring_items（评分台账）时：大纲必须让每个评分项至少有一个章节实质响应；响应某评分项的章节，其 summary 末尾以「响应得分点：<评分项标题>」点名（多项用顿号分隔，标题照抄台账原文不得改写）。技术类评分项一项都不能漏——确无现成章节可安放的（如逐条应答类评分项）必须增设专门章节承接，不得静默丢弃。"""

# 兜底重试时追加在 SPEC_USER_TEMPLATE 之后：把首轮不合格的事实喂回去，要求按规则 2 重排层级
SPEC_FLAT_RETRY_ADDENDUM = """

## 上一次输出不合格，必须修正后重新输出
上一次生成的大纲把全部 __FLAT_COUNT__ 个章节都标成了 level=1，没有任何二级/三级小节，违反规则 2。
请重新规划：归并为 6-12 个一级大章（level=1），其余章节作为所属大章下的二级小节（level=2，复杂处可设 level=3），
数组顺序保持「大章 → 其下小节」的目录顺序；总字数区间与其他规则不变。"""

# 评分覆盖重试（2026-10-25 复盘轮）：首轮大纲漏响应技术类评分项时，点名漏项要求补齐
_SPEC_COVERAGE_RETRY_ADDENDUM = """

## 上一次输出不合格，必须修正后重新输出
上一次生成的大纲没有任何章节响应下列技术类评分项（规则 7 要求每个评分项至少一个章节实质响应，
并在 summary 末尾以「响应得分点：<标题>」点名；确无现成章节安放的必须增设专门章节承接）：
__UNCOVERED_ITEMS__
请重新输出完整大纲：为上述每个评分项安排或指定响应章节，其余结构与内容尽量保持不变；
总字数区间、多级目录与其他规则不变。"""

REVISE_SYSTEM_PROMPT = (
    "你是资深投标文件编写专家。按用户的修改指令调整编写大纲（Spec），保持未涉及的部分不变。"
    "只输出一个合法 JSON 数组（完整的新大纲），不要输出任何其他文字或代码块标记。"
)

REVISE_USER_TEMPLATE = """当前 Spec：

__CURRENT_SPEC__

生成要求（用户设定，修订后的大纲仍须满足）：
__GENERATION_CONSTRAINTS__

用户修改指令：
__INSTRUCTION__

请输出调整后的完整 Spec JSON 数组，字段格式与输入一致（title/level/summary/article_count/text_count/charts）；保持多级目录结构（大章 level=1、小节 level=2/3，level 如实标注所属层级），除非用户指令明确要求改变层级。"""

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

_WIZARD_SECTION_SYSTEM_PROMPT_BASE = (
    "你是资深投标文件编写专家。撰写指定章节的正文，严格遵循本章摘要与图表计划，"
    "内容专业、具体、结构清晰、可直接用于投标文件。"
    "只输出 Markdown 正文：不要输出本节标题（系统会自动添加），不要输出解释或前言，"
    "不要把整节内容包进代码块围栏（mermaid 图表除外）。"
    "公司资质、业绩、项目、人员与数据必须来自「素材摘录」，不得虚构；"
    "素材与需求都未覆盖的事实性内容用“（请补充）”占位，由投标人自行补齐。"
)
# 措辞铁律（22 号 SOP §六反向移植；2026-10-24 用户反馈成稿"AI 味浓厚"）：
# 去 AI 味的关键在生成侧——起草与修订共用同一份规则（模式 A：不让分节代理各说各话）
_WIZARD_WORDING_RULES = (
    "措辞铁律（投标正式文体，硬约束）："
    "①用具体做法、指标、依据、时间安排说话，每个自然段都要有信息量；"
    "严禁无事实支撑的自夸句与形容词堆砌"
    "（如「全方位、多维度、一站式、赋能、助力、打造、强大的、优秀的、完善的」）；"
    "②不写模板腔过渡与套话：段首不用「首先/其次/最后」串联成文，"
    "结尾不写「综上所述/总而言之」式升华，不用口号式排比与空泛展望收尾，段落直接点题；"
    "③语气客观正式，不出现「众所周知、毋庸置疑、我们相信」等口语化或煽情表达；"
    "④对招标要求的直接响应优先于泛泛的公司能力介绍；"
    "⑤正文只是投标文件本体，不是写作说明：严禁输出思考/推理过程，"
    "严禁解释「为什么这样写」的写作说明（如「本章将介绍」「下面从几个方面展开」「为了让评标专家了解」），"
    "严禁与读者对话（「好的，」「以下是」「接下来我们」「希望」等表述）与任何 AI 身份表述"
    "（「作为AI」）；严禁把标书编制过程的内部机制写进正文"
    "（如「需求台账」「判定点清单」「内部核查留档」「母本/模板口径」等编制方法论表述），"
    "正文只描述交付给采购人的系统、方案与承诺本身；方案自身的依据与优势用陈述句直接给出，不属于此列。"
    "⑥业绩与产能事实只能出自素材摘录：严禁引用无素材出处的内部案例/产能数据"
    "（如「N 个工作日内形成 N 页正文、N 张表格、N 万字成稿」类自证数字——无法举证又易被质疑夸大），"
    "素材未覆盖的产能事实一律不写。"
    "⑦素材或内部资料里我方的实现细节不得原样写入正文：配置项/参数名、系统错误码、"
    "内部测试环境的运行数据与调优过程、计费与运营机制等，对外只转化为采购人可感知的"
    "能力、指标与承诺表述（需量化时给可兑现的建议值）；正文出现代码风格标识符即视为缺陷。"
    "⑧全稿体例统一：图表按章编号（表X-Y／图X-Y，X=一级章号）且编号与图题一致；"
    "交付时间锚点全稿统一用「合同签订（T0）后 X 日内」表述，引用招标文件的绝对日期节点时"
    "须同时写明与相对周期不一致时以对采购人更严格者为准；同一技术机制跨章涉及时一处主述、"
    "他处简述并以章节号引用主述处，不得各写一套。"
    "⑨「（请补充）」占位只写在正文段落句内：表格单元格、列名、图注与图内节点标签一律不写占位，"
    "该处的待定信息改为在邻近正文段落说明「于需求确认阶段与采购人商定」或直接给出可兑现的建议值。"
)
_WIZARD_SECTION_CHART_RULES = (
    "图表要求："
    "①结构化内容（对比、参数、人员配置、职责分工、进度安排）优先用 Markdown 表格；"
    "②「本章图表计划」指定了 mermaid 图的，按计划的类型与标题输出 ```mermaid 代码块，"
    "语法必须严格合法，节点与标签使用中文，整段代码块独占成块、前后留空行；"
    "③图表涉及的名称、日期、数值必须与正文和素材一致，严禁虚构；"
    "④纯论述性内容保持文本段落，不要为凑图表而强行图示化。"
)
# 生成要求「不配图」（决策 41）：章节正文不得出现任何表格/图示，结构化内容改文字或列表
_WIZARD_SECTION_NO_CHART_RULES = (
    "图表要求：用户在生成要求中选择了不配图——本章不得输出任何 Markdown 表格、mermaid 代码块"
    "或其他图示；对比、参数、人员配置、职责分工、进度安排等结构化内容一律用文字段落或有序/无序列表表达。"
)
WIZARD_SECTION_SYSTEM_PROMPT = _WIZARD_SECTION_SYSTEM_PROMPT_BASE + _WIZARD_SECTION_CHART_RULES


def section_system_prompt(*, charts_enabled: bool = True, fig_mode: str = "mermaid") -> str:
    # fig_mode=bidfig（23 号 O8）：切 V1 bidfig 系统 prompt（LLM 输出结构化 JSON 围栏，
    # 服务端原语库确定性渲染）；不配图时图表规则以 charts_enabled 为准（图都不要就更无引擎可言）
    # 措辞铁律（2026-10-24）两种模式都注入——bidfig 版复自旧 draft prompt，本身没有措辞约束
    if charts_enabled and fig_mode == "bidfig":
        return SECTION_SYSTEM_PROMPT_BIDFIG + _WIZARD_WORDING_RULES
    rules = _WIZARD_SECTION_CHART_RULES if charts_enabled else _WIZARD_SECTION_NO_CHART_RULES
    return _WIZARD_SECTION_SYSTEM_PROMPT_BASE + _WIZARD_WORDING_RULES + rules

WIZARD_SECTION_USER_TEMPLATE = """## 项目招标要素（摘要）
__ANALYSIS_JSON__

## 编写需求（用户确认）
__REQUIREMENTS_TEXT__

## 投标文件大纲（整体结构）
__OUTLINE_TEXT__

## 本次撰写章节
标题：__TITLE__
本章摘要：__SUMMARY__
本章响应的评分项
__SCORE_ITEMS_TEXT__
本章图表计划：
__CHART_PLAN_TEXT__
篇幅（硬约束）：全章正文约 __ARTICLE_COUNT__ 段、每段约 __TEXT_COUNT__ 字，总字数目标约 __WORD_BUDGET__ 字（表格与图注文字一并计入）。写完自查：超出目标 130% 必须精简——删重复展开与空泛修饰、合并同类表格，优先保留评分项响应、关键指标与承诺；不足目标 70% 才补充展开；严禁为凑篇幅灌水。

## 素材摘录（公司真实资料，事实性内容只能出自这里）
__MATERIAL_CHUNKS__

## 已完成章节（保持前后呼应，不要重复）
__PREV_SECTIONS__

请撰写该章节正文（Markdown，不含本节标题）。"""

# 父章节与子节同批生成时，父节点只写章首导语（2026-10-22 生产发现：章级节点按全量正文
# 撰写会自带一套编号小节，与随后单独生成的子节内容成对重复——同一主题在成稿里出现两遍）。
_WIZARD_CHAPTER_INTRO_SYSTEM_PROMPT = (
    "你是资深投标文件编写专家。本节点是大纲中的父章节，其下的小节将由系统另行单独撰写、"
    "紧接在本导语之后插入投标文件，因此你只负责撰写「章首导语」："
    "概述本章在整份投标文件中的定位、本章覆盖的招标要求与响应思路，"
    "并用一两句话按标题预告本章各小节的安排。"
    "严禁展开任何小节的正文内容；严禁输出任何标题（含编号小节标题）、表格、mermaid 图或代码块围栏；"
    "只输出 1-2 个自然段的导语正文。不要输出本章标题（系统会自动添加），不要输出解释或前言。"
    "导语只作结构性概述，不引用具体证书、业绩、人员与金额，不虚构任何事实。"
    "导语用实在话概括定位与安排，严禁口号式套话与形容词堆砌（措辞铁律同章节正文）。"
    "不与读者对话、不解释写法、不输出思考过程——导语直接是投标文件正文。"
)

WIZARD_CHAPTER_INTRO_USER_TEMPLATE = """## 项目招标要素（摘要）
__ANALYSIS_JSON__

## 编写需求（用户确认）
__REQUIREMENTS_TEXT__

## 投标文件大纲（整体结构）
__OUTLINE_TEXT__

## 本次撰写：章首导语（本节点为父章节）
章节标题：__TITLE__
本章摘要：__SUMMARY__
本章响应的评分项（导语中说明本章如何回应，不展开正文）
__SCORE_ITEMS_TEXT__
本章后续小节（由系统另行单独撰写，导语中只按标题预告，严禁展开其内容）：
__CHILD_TITLES__
篇幅：__ARTICLE_COUNT__ 段，每段约 __TEXT_COUNT__ 字。

## 已完成章节（保持前后呼应，不要重复）
__PREV_SECTIONS__

请撰写本章导语正文（纯文字自然段，不含任何标题、表格与图示）。"""


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


def estimate_points_for_usage(
    prompt_tokens: int, completion_tokens: int, multiplier: Decimal | float
) -> Optional[int]:
    """(prompt, completion)→点数线性预估（与结算同公式 cost×10×倍率，向上取整）。

    O5 审读轮预估与素材索引预估共用的底层口径；价目缺失返回 None（前端退回 token 提示）。
    """
    from backend.services.billing import sales_points_for
    from backend.services.cost_calculator import estimate_cost

    provider, model = current_llm_for_pricing()
    cost = estimate_cost(
        provider=provider,
        model=model,
        prompt_tokens=max(1, int(prompt_tokens or 0)),
        completion_tokens=max(0, int(completion_tokens or 0)),
        status="success",
    )
    if cost is None:
        return None
    points = sales_points_for(cost, multiplier)
    if points <= 0:
        return None
    return int(points.to_integral_value(rounding=ROUND_CEILING))


def estimate_index_points(estimated_tokens: int, multiplier: Decimal | float) -> Optional[int]:
    """token→点数线性预估（上传前确认弹窗显示「约 X 点」，§4.2/story 7）。

    口径：索引=1 次 LLM 调用/素材——prompt≈全文 tokens，completion≈元数据输出，
    按 prompt 的 8%、下限 200 tokens 粗估（底层换算见 estimate_points_for_usage）。
    """
    completion = max(200, int((estimated_tokens or 0) * 0.08))
    return estimate_points_for_usage(estimated_tokens, completion, multiplier)


def estimate_review_usage(total_words: int) -> tuple[int, int]:
    """O5 审读轮用量预估口径：(prompt_tokens, completion_tokens)。

    审读读全稿（字数×0.7×1.2 含上下文开销、completion≈3%）；修订按 30% 章节重写假设
    （prompt=30% 正文+上下文、completion≈同量正文）。量级提示，实际按真实用量结算。
    """
    words = max(0, int(total_words or 0))
    read_tokens = int(words * ESTIMATE_TOKENS_PER_CHAR * 1.2)
    prompt = read_tokens + int(read_tokens * 0.3 * 1.2)
    completion = max(200, int(read_tokens * 0.03) + int(read_tokens * 0.3))
    return prompt, completion


def estimate_review_points(total_words: int, multiplier: Decimal | float) -> Optional[int]:
    """O5 审读轮点数预估（发起前确认弹窗）；口径见 estimate_review_usage。"""
    prompt, completion = estimate_review_usage(total_words)
    return estimate_points_for_usage(prompt, completion, multiplier)


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


# ------------------------------------------------------------------ 历史标书目录参照（23 号 O7）

# 历史模板类素材的识别口径：本地上传传分类 id（HistoryDoc），客户端上传传分类名
# （素材-历史模板）——两种形态都可能是历史标书，按包含匹配
HISTORY_TEMPLATE_CATEGORY_MARKERS = ("历史模板", "HistoryDoc")
# 索引文件顶部的目录结构小节标题（generate_spec 侧按此识别并提取参照）
TOC_REFERENCE_HEADING = "## 历史标书目录结构（仅作层级参照）"
_TOC_MAX_HEADINGS = 200
_TOC_MAX_CHARS = 6_000
# spec prompt 注入的历史目录总量上限（多份素材合并后仍要给正题留上下文）
TOC_REFERENCE_MAX_CHARS = 8_000
TOC_REFERENCE_MAX_DOCS = 5


def is_history_template_category(category: str | None) -> bool:
    """素材分类是否属「历史模板」（S7 目录参照的触发条件）。"""
    text = str(category or "").strip()
    return bool(text) and any(marker in text for marker in HISTORY_TEMPLATE_CATEGORY_MARKERS)


def extract_toc_tree(markdown: str, *, max_headings: int = _TOC_MAX_HEADINGS) -> str:
    """从素材解析产物提取标题树（纯代码、零 LLM）：# 层级缩进列表，供大纲生成参照。

    只取 1-4 级标题（投标文件目录深度），剥编号前缀保持与 Spec 标题可比对；
    空标题/超限截断以「…（共 N 级标题，已截断）」收口。
    """
    lines: list[str] = []
    total = 0
    for raw_line in (markdown or "").splitlines():
        match = _HEADING_RE.match(raw_line.strip())
        if not match:
            continue
        level = len(match.group(1))
        if level > 4:
            continue
        total += 1
        if len(lines) >= max_headings:
            continue
        lines.append("%s- %s" % ("  " * (level - 1), match.group(2).strip()[:120]))
    if not lines:
        return ""
    body = "\n".join(lines)[:_TOC_MAX_CHARS]
    if total > max_headings:
        body += "\n…（共 %d 个标题，超出部分已截断）" % total
    return body


def build_toc_reference_block(entries: list[tuple[str, str, str]]) -> str:
    """汇总素材索引里的「历史标书目录结构」小节 → spec prompt 注入块（O7）。

    entries = load_material_index_texts 的产物 (document_id, filename, index_md)；
    只认 TOC_REFERENCE_HEADING 起、到下一个二级标题止的小节；无历史模板素材返回空串
    （SPEC_USER_TEMPLATE 的 __TOC_REFERENCE__ 占位替换为空，模板行为与存量一致）。
    """
    blocks: list[str] = []
    for doc_id, name, index_md in entries:
        if len(blocks) >= TOC_REFERENCE_MAX_DOCS:
            break
        text = index_md or ""
        start = text.find(TOC_REFERENCE_HEADING)
        if start < 0:
            continue
        rest = text[start + len(TOC_REFERENCE_HEADING):]
        end = rest.find("\n## ")
        section = (rest if end < 0 else rest[:end]).strip()
        if section:
            blocks.append("### %s\n%s" % (name or doc_id, section))
    if not blocks:
        return ""
    joined = "\n\n".join(blocks)[:TOC_REFERENCE_MAX_CHARS]
    return (
        "\n## 历史标书目录参照（仅作层级与覆盖面参照，理解同类标书通常包含哪些板块；"
        "严禁照抄其中任何章节名与内容，大纲必须基于本次招标要求与素材规划）\n" + joined + "\n"
    )


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
    # 2026-10-23 商务屏蔽第二道防线：模型漏看指令问出商务问题（资质/业绩/报价/商务条款等）直接丢弃
    questions = [item for item in questions if not is_business_question(item)]
    return {"questions": questions}


def merge_questionnaire_answers(
    questionnaire: dict[str, Any] | None, answers: list[dict[str, Any]]
) -> dict[str, Any]:
    """Merge user answers into a stored questionnaire → 编写需求 JSON (pure).

    effective_answer：answered 取用户输入；adopted 取建议答案；skipped 置空
    （skipped 的问题在下游 prompt 中整条省略，不进入编写需求）；
    supplemented 表示用户已为该问题补充新素材（答案待下一轮检查，effective 同样置空，
    但 build_requirements_text 会渲染显式标记供下一轮 prompt 识别）。
    """
    questions = list((questionnaire or {}).get("questions") or [])
    by_id = {str(item.get("id")): item for item in questions if isinstance(item, dict)}
    for answer in answers or []:
        question = by_id.get(str(answer.get("question_id")))
        if question is None:
            continue
        action = answer.get("action")
        if action not in ("answered", "adopted", "skipped", "supplemented"):
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
    # 生成要求（决策 38）置顶：下游 prompt（问卷追问轮/追问侧栏/章节撰写）都要知道范围与篇幅
    options = generation_options_of(requirements)
    if options:
        word_count = options.get("word_count")
        lines.append(
            "- [生成要求] 生成内容："
            f"{generation_parts_label(options.get('parts') or [])}；"
            f"目标总字数：{f'{word_count:,} 字' if word_count else '未设置'}；"
            f"配图：{'是' if options.get('charts', True) else '否'}"
        )
    # 口径卡（23 号 O4）：统一称谓/公司名/项目名/禁词——注入问卷追问轮/Spec/章节全部下游 prompt
    style_line = build_style_card_text((requirements or {}).get("style_card"))
    if style_line:
        lines.append(style_line)
    # 应答口径（23 号 O3）：已确认的三态口径按措辞模板注入；未确认（AI 建议草稿）不注入
    lines.extend(build_capability_lines((requirements or {}).get("capability_map")))
    for question in (requirements or {}).get("questions") or []:
        if not isinstance(question, dict):
            continue
        # 2026-10-23 商务屏蔽：存量 requirements 里已答的商务问答不再进入下游 prompt
        if is_business_question(question):
            continue
        label = str(question.get("question") or "")[:200]
        if question.get("action") == "supplemented":
            # 已补充素材的问题：答案还没有（待下一轮检查），但要让下游 prompt 知道用户补了素材
            lines.append(f"- [已补充素材] {label}：用户已为该问题补充新素材，答案待下一轮检查确认")
            continue
        effective = str(question.get("effective_answer") or "").strip()
        if not effective:
            continue  # skipped / 未回答的不进入下游
        topic = str(question.get("topic") or "其他")
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


# ------------------------------------------------------------------ 应答口径（23 号 O3）+ 口径卡（O4）

# 三态口径：existing=现有已实现（须有素材依据）/ build=本项目建设内容 / confirm=待用户确认
CAPABILITY_STANCES = ("existing", "build", "confirm")
CAPABILITY_MAX_ITEMS = 30
# 口径卡统一称谓候选（与 bid_doc_checks._SELF_REFERENCE_VARIANTS 对齐）
SELF_REFERENCE_CHOICES = ("我司", "我公司", "我方", "本公司")
_STYLE_CARD_FORBIDDEN_MAX = 12


def normalize_style_card(raw: Any) -> dict[str, Any] | None:
    """口径卡归一（O4）：{company_name, project_short_name, self_reference, forbidden_words, notes}。

    非 dict/全空返回 None（存量无口径卡语义）；self_reference 不在候选池时回退「我司」；
    forbidden_words 去重限长（脱敏词表只放短词，防把整句塞进机检 count）。
    """
    if not isinstance(raw, dict):
        return None
    self_reference = str(raw.get("self_reference") or "").strip()
    if self_reference not in SELF_REFERENCE_CHOICES:
        self_reference = "我司"
    words: list[str] = []
    for item in raw.get("forbidden_words") or []:
        word = str(item or "").strip()[:50]
        if word and word not in words and len(words) < _STYLE_CARD_FORBIDDEN_MAX:
            words.append(word)
    card = {
        "company_name": str(raw.get("company_name") or "").strip()[:200],
        "project_short_name": str(raw.get("project_short_name") or "").strip()[:200],
        "self_reference": self_reference,
        "forbidden_words": words,
        "notes": str(raw.get("notes") or "").strip()[:500],
    }
    if not any((card["company_name"], card["project_short_name"], card["notes"], words)):
        # 只有默认称谓也算有效口径卡（统一称谓是 O4 机检的驱动项）
        return card if raw.get("self_reference") else None
    return card


def build_style_card_text(style_card: Any) -> str:
    """口径卡 → 注入 prompt 的单行文本（O4：问卷追问轮/Spec/章节 prompt 共用）。"""
    card = normalize_style_card(style_card)
    if card is None:
        return ""
    parts = [f"我方称谓全稿统一用「{card['self_reference']}」，不得混用其他自称"]
    if card["company_name"]:
        parts.append(f"公司名统一为「{card['company_name']}」")
    if card["project_short_name"]:
        parts.append(f"项目名统一用「{card['project_short_name']}」")
    if card["forbidden_words"]:
        parts.append("禁用词（出现即扣分，一律改写）：" + "、".join(card["forbidden_words"]))
    if card["notes"]:
        parts.append(card["notes"])
    return "- [口径卡] " + "；".join(parts)


def build_capability_lines(capability_map: Any) -> list[str]:
    """已确认口径 → 章节措辞规则行（O3 措辞模板注入；未确认条目不注入）。

    existing→「本系统已实现」+引用素材依据；build→「本项目建设内容」+实施交付安排；
    confirm→不得写具体能力参数，用（请补充）占位（不虚构）。
    """
    if not isinstance(capability_map, list):
        return []
    lines: list[str] = []
    for raw in capability_map[:CAPABILITY_MAX_ITEMS]:
        if not isinstance(raw, dict) or raw.get("confirmed") is not True:
            continue
        stance = str(raw.get("stance") or "")
        if stance not in CAPABILITY_STANCES:
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        requirement = str(raw.get("requirement") or "").strip()
        label = "%s（%s）" % (title[:120], requirement[:120]) if requirement else title[:120]
        if stance == "existing":
            evidence = str(raw.get("evidence") or "").strip()
            basis = f"，依据素材「{evidence[:100]}」中的能力事实" if evidence else "，须引用素材池中的真实能力事实"
            lines.append(f"- [口径-现有已实现] {label}：表述为「本系统已实现」{basis}")
        elif stance == "build":
            lines.append(f"- [口径-本项目建设] {label}：表述为「本项目建设内容」，并给出实施与交付阶段安排")
        else:
            lines.append(f"- [口径-待确认] {label}：未确认前不得写具体能力参数与指标，用（请补充）占位，严禁虚构")
    return lines[:12]


def normalize_capability_map(raw: Any) -> list[dict[str, Any]]:
    """能力口径映射归一（O3）：[{title, requirement, stance, evidence, note, confirmed}]。

    stance 非法回退 confirm（宁待确认不虚构）；商务/报价类条目直接丢弃（产品口径仅技术标，
    复用评分项商务判定词表）；confirmed 保持入参值（生成建议=False，保存确认=True）。
    """
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for entry in raw[:CAPABILITY_MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        requirement = str(entry.get("requirement") or "").strip()
        if is_business_scoring_item(title, requirement):
            continue
        stance = str(entry.get("stance") or "").strip()
        if stance not in CAPABILITY_STANCES:
            stance = "confirm"
        items.append(
            {
                "title": title[:200],
                "requirement": requirement[:500],
                "stance": stance,
                "evidence": str(entry.get("evidence") or "").strip()[:200],
                "note": str(entry.get("note") or "").strip()[:500],
                "confirmed": bool(entry.get("confirmed")),
            }
        )
    return items


CAPABILITY_SYSTEM_PROMPT = (
    "你是资深投标文件专家。基于招标要求与公司素材索引，逐项给出「应答口径」建议——"
    "对每条技术类招标要求判断我方应答姿态：existing（我方现有系统已实现，须给出素材依据）、"
    "build（作为本项目建设内容交付）或 confirm（素材不足、需投标人确认后再定）。"
    "同时顺带产出「口径卡」：我方统一称谓/公司名/项目名/禁用词。"
    "只输出一个合法 JSON 对象，不要输出任何其他文字或代码块标记。"
)

CAPABILITY_USER_TEMPLATE = """## 招标要求（逐项）
__REQUIREMENT_LIST__

## 技术类评分项（如有）
__SCORING_ITEMS__

## 公司素材索引（每行一段：编号｜标题｜摘要｜关键词）
__MATERIAL_INDEX__

请输出 JSON：

{"capability_map": [
  {"title": "招标要求短语", "requirement": "该要求的具体内容，一句话",
   "stance": "existing|build|confirm",
   "evidence": "素材依据（如 '素材名#3'，existing 必填且必须是素材索引里真实存在的条目；其余留空）",
   "note": "口径说明（一句话，如按 existing 写时的能力事实概要）"}
],
 "style_card": {
  "self_reference": "我司|我公司|我方|本公司（全稿统一称谓，从素材/需求里出现的自称里选最常用的）",
  "company_name": "公司名（素材/招标文件可查则填，查不到留空）",
  "project_short_name": "项目简称（招标文件可查则填，查不到留空）",
  "forbidden_words": ["禁用词（如招标文件明令或评标忌讳的表述，最多几个；没有给空数组）"],
  "notes": "其他统一口径说明，没有留空"}}

规则：
1. capability_map 只覆盖技术类要求（技术方案/实施/人员/培训/售后/检测/标准规范等）——资质、业绩、财务、报价、商务条款类一律不出（商务标不在生成范围）；
2. stance 判定必须诚实：素材索引里有能对应上的能力事实才给 existing 并如实填 evidence，否则给 build 或 confirm——严禁把没有素材依据的能力标成 existing；
3. 素材覆盖不了的给 confirm，note 里写清缺什么信息；
4. 最多 __MAX_ITEMS__ 项，按招标要求重要度排序；严禁虚构素材里不存在的内容。"""


async def generate_capability_map(
    llm: WizardLLM,
    *,
    analysis: dict[str, Any],
    material_index_text: str,
) -> dict[str, Any]:
    """能力口径建议 + 口径卡（O3/O4 同一次 LLM 调用顺带产出，零额外成本）。"""
    requirements = [
        str(item or "").strip() for item in (analysis or {}).get("tender_requirements") or [] if str(item or "").strip()
    ]
    scoring_items = [
        item for item in scoring_items_of(analysis) if not item.get("is_business")
    ]
    scoring_text = (
        "\n".join(f"- {item['title']}（{item['weight']}）：{item['criteria']}" for item in scoring_items)
        or "（无）"
    )
    user_prompt = (
        CAPABILITY_USER_TEMPLATE.replace(
            "__REQUIREMENT_LIST__",
            "\n".join(f"- {text[:300]}" for text in requirements[:30]) or "（无）",
        )
        .replace("__SCORING_ITEMS__", scoring_text)
        .replace("__MATERIAL_INDEX__", material_index_text or "（无素材）")
        .replace("__MAX_ITEMS__", str(CAPABILITY_MAX_ITEMS))
    )
    payload = await llm.generate_json(CAPABILITY_SYSTEM_PROMPT, user_prompt)
    if not isinstance(payload, dict):
        raise RuntimeError("口径建议生成结果不是 JSON 对象，请重试")
    result = normalize_capability_map(payload.get("capability_map"))
    if not result:
        raise RuntimeError("口径建议生成结果为空，请重试")
    return {"capability_map": result, "style_card": normalize_style_card(payload.get("style_card"))}


def capability_stance_issues(
    spec: list[dict[str, Any]],
    done: list[dict[str, Any]],
    capability_map: Any,
) -> list[dict[str, str]]:
    """口径红线联动（O3 机检扩展）：口径为 existing 但无素材依据的章节 → warning。

    只查已确认（confirmed）条目——AI 建议草稿尚未拍板，不进机检；
    命中章节（标题匹配口径条目标题，口径含祖先链）逐章报，无命中章节时报全稿级一条。
    """
    if not isinstance(capability_map, list):
        return []
    issues: list[dict[str, str]] = []
    for entry in capability_map:
        if not isinstance(entry, dict) or entry.get("stance") != "existing":
            continue
        if entry.get("confirmed") is not True:
            continue
        if str(entry.get("evidence") or "").strip():
            continue
        title = str(entry.get("title") or "").strip()
        key = _norm_match_key(title)
        if not key:
            continue
        matched: list[str] = []
        for item in done:
            chain_text = "".join(
                _norm_match_key(f"{node.get('title') or ''}{node.get('summary') or ''}")
                for node in _ancestors_of(spec, str(item.get("node_id") or ""))
            )
            if key in chain_text:
                matched.append(str(item.get("node_id") or ""))
        detail = (
            "「%s」口径为「现有已实现」但未给出素材依据——existing 口径必须有素材支撑，"
            "请补充素材证据或把口径改判为「本项目建设」/「待确认」" % title[:60]
        )
        for node_id in matched[:5]:
            issues.append({"severity": "warning", "node_id": node_id, "type": "capability_no_evidence", "detail": detail})
        if not matched:
            issues.append({"severity": "warning", "node_id": "", "type": "capability_no_evidence", "detail": detail})
        if len(issues) >= 10:
            break
    return issues


# ------------------------------------------------------------------ 评分台账（23 号 O2：S1 提取/S9 覆盖核查）


def is_business_scoring_item(title: str, criteria: str = "") -> bool:
    """评分项是否属商务/报价范畴（23 号批次五 E3）——词表口径复用问卷侧
    is_business_question（硬词命中即商务；资质/业绩等宽泛词需无技术语境）。
    商务项由投标人线下准备，技术章不认领商务分（E2E 实测"报价 60 分"
    被产品商务标模块设计章误认领为已覆盖）。"""
    text = "%s %s" % (title or "", criteria or "")
    if any(keyword in text for keyword in _BUSINESS_QUESTION_HARD_KEYWORDS):
        return True
    return any(keyword in text for keyword in _BUSINESS_QUESTION_BROAD_KEYWORDS) and not _TECHNICAL_CONTEXT_RE.search(
        text
    )


def scoring_items_of(analysis: dict[str, Any] | None) -> list[dict[str, str]]:
    """从招标要素里取结构化评分台账；旧向导/无评分办法时为空列表（全链路判空跳过，决策 42 口径）。"""
    if not isinstance(analysis, dict):
        return []
    items = analysis.get("scoring_items")
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items[:30]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        criteria = str(item.get("criteria") or "").strip()
        result.append(
            {
                "title": title,
                "weight": str(item.get("weight") or "").strip(),
                "criteria": criteria,
                # 商务/报价类评分项（E3）：不注入章节 prompt、覆盖矩阵不认领
                "is_business": is_business_scoring_item(title, criteria),
            }
        )
    return result


def _ancestors_of(spec: list[dict[str, Any]], node_id: str) -> list[dict[str, Any]]:
    """按 node_id 前缀取自身+全部祖先节点（"5.3.1" → ["5", "5.3", "5.3.1"]）。"""
    result: list[dict[str, Any]] = []
    for node in spec:
        other = str(node.get("node_id") or "")
        if other and (node_id == other or node_id.startswith(other + ".")):
            result.append(node)
    return result


def _norm_match_key(text: str) -> str:
    return re.sub(r"[\s，。、:：;；()（）\-—]+", "", text or "")


def score_items_for_node(
    spec: list[dict[str, Any]], node: dict[str, Any], scoring_items: list[dict[str, str]]
) -> list[dict[str, str]]:
    """该节点命中的评分项：评分项标题出现在本节点或任一祖先的标题/摘要里（spec 生成规则 7 的回查）。"""
    if not scoring_items:
        return []
    chain_text = "".join(
        _norm_match_key(f"{n.get('title') or ''}{n.get('summary') or ''}")
        for n in _ancestors_of(spec, str(node.get("node_id") or ""))
    )
    hits: list[dict[str, str]] = []
    for item in scoring_items:
        if item.get("is_business"):
            continue  # 商务得分点不注入技术章 prompt（E3）：商务标内容由投标人线下准备
        key = _norm_match_key(item["title"])
        if key and key in chain_text:
            hits.append(item)
    return hits


def render_score_items_text(items: list[dict[str, str]]) -> str:
    if not items:
        return "（无直接对应的评分项——按大纲摘要与编写需求撰写即可，不要虚构评分要求）"
    lines = []
    for item in items:
        weight = f"（{item['weight']}）" if item["weight"] else ""
        criteria = f"：{item['criteria']}" if item["criteria"] else ""
        lines.append(f"- {item['title']}{weight}{criteria}")
    return (
        "本章必须实质回应下列评分项的评审标准（评标专家会按这些条目打分）：\n"
        + "\n".join(lines)
    )


def rejection_items_text(analysis: dict[str, Any] | None) -> str:
    """★废标/否决条款清单 → 注入审读/修订 prompt（★响应表条款编号列的映射来源，
    第 2 轮质量轮：条款编号裸（请补充）导致评委无法逐条对照）。旧向导无该字段时为空。
    """
    items = analysis.get("rejection_items") if isinstance(analysis, dict) else None
    if not isinstance(items, list):
        return ""
    lines: list[str] = []
    for raw in items[:30]:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        requirement = str(raw.get("requirement") or "").strip()
        if not title and not requirement:
            continue
        lines.append(f"- {title[:120]}：{requirement[:160]}" if requirement else f"- {title[:120]}")
    return "\n".join(lines)[:2_000]


def tender_requirements_text(analysis: dict[str, Any] | None) -> str:
    """招标要求原文 → 注入审读/修订 prompt（第 3 轮质量轮：审读意见凭臆断「招标文件只明确
    2026-12-10 一个锚点」把招标明文工期 150 天降格为我方建议工期——Reader/Revise 必须能
    对照招标原文核验数值类意见，审读事实断言须可核验）。旧向导无该字段时为空。
    """
    items = analysis.get("tender_requirements") if isinstance(analysis, dict) else None
    if not isinstance(items, list):
        return ""
    lines = [f"- {str(raw or '').strip()[:200]}" for raw in items[:20] if str(raw or '').strip()]
    return "\n".join(lines)[:3_000]


def score_coverage_matrix(
    spec: list[dict[str, Any]],
    generated_node_ids: list[str],
    scoring_items: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """得分点覆盖矩阵（S9 判定点核查）：每个评分项被哪些已生成章节响应（自身或祖先命中）。"""
    matrix: list[dict[str, Any]] = []
    generated = [str(node_id) for node_id in generated_node_ids or []]
    for item in scoring_items:
        is_business = bool(item.get("is_business"))
        key = _norm_match_key(item["title"])
        covered_by: list[str] = []
        # 商务项不认领（E3）：covered_by 恒空、前端覆盖卡标"商务人工准备"——
        # 技术章写的是产品/方案实现，不等于响应了本标书的商务得分点
        if key and not is_business:
            for node_id in generated:
                chain_text = "".join(
                    _norm_match_key(f"{n.get('title') or ''}{n.get('summary') or ''}")
                    for n in _ancestors_of(spec, node_id)
                )
                if key in chain_text:
                    covered_by.append(node_id)
        matrix.append(
            {
                "title": item["title"],
                "weight": item["weight"],
                "criteria": item["criteria"],
                "is_business": is_business,
                "covered_by": covered_by,
            }
        )
    return matrix


# 评分覆盖硬门禁（2026-10-25 真实成稿复盘：技术类最高分项「技术规范书应答 12分」
# covered_by 为空，系统自己知道却无任何拦截——12 分裸奔）。两层防线：
# ① generate_spec 覆盖重试（见 _SPEC_COVERAGE_RETRY_ADDENDUM）；② spec confirm 接口
# 对未覆盖的技术类评分项 422 阻断（用户在正确阶段补章节，写作前拦截=费用止损）。
# 证书/资质/业绩类评分项即使被误判为技术类也不阻断（正文给不了扫描件，阻断只会卡死流程）。
_CERTIFICATE_LIKE_SCORING_RE = re.compile(r"证书|资质|业绩|扫描件|发票|合同|审计|财务|营业执照")


def uncovered_scoring_items(
    spec: list[dict[str, Any]], scoring_items: list[dict[str, str]]
) -> list[dict[str, str]]:
    """技术类（非商务）评分项中没有任何章节响应的清单（覆盖判定与覆盖矩阵同口径）。"""
    uncovered: list[dict[str, str]] = []
    for item in scoring_items or []:
        if item.get("is_business"):
            continue
        key = _norm_match_key(item["title"])
        if not key:
            continue
        covered = any(
            key
            in "".join(
                _norm_match_key(f"{n.get('title') or ''}{n.get('summary') or ''}")
                for n in _ancestors_of(spec, str(node.get("node_id") or ""))
            )
            for node in spec
            if str(node.get("node_id") or "")
        )
        if not covered:
            uncovered.append(item)
    return uncovered


def gate_blocking_scoring_items(
    spec: list[dict[str, Any]] | None, analysis: dict[str, Any] | None
) -> list[dict[str, str]]:
    """confirm 硬门禁的阻断清单＝未覆盖 且 非证书/资质类的技术评分项。"""
    if not isinstance(spec, list) or not spec:
        return []
    uncovered = uncovered_scoring_items(spec, scoring_items_of(analysis))
    return [
        item
        for item in uncovered
        if not _CERTIFICATE_LIKE_SCORING_RE.search("%s %s" % (item.get("title") or "", item.get("criteria") or ""))
    ]


# ------------------------------------------------------------------ 图表加固（23 号批次五 E1/E2）


def fig_stats_of(done: list[dict[str, Any]], fig_mode: str) -> dict[str, Any]:
    """全稿图表统计（E1 观察）：mermaid/bidfig/降级块计数进 summary.fig_stats，
    前端质检区展示；bidfig 模式下 mermaid 占比是批次五验收指标（E2E 实测基线 29%）。"""
    stats: dict[str, Any] = {"fig_mode": fig_mode, "mermaid": 0, "bidfig": 0, "degraded": 0}
    nodes: dict[str, dict[str, int]] = {}
    for item in done:
        content = str(item.get("content") or "")
        mermaid = len(extract_mermaid_fences(content))
        bidfig = len(extract_bidfig_fences(content))
        degraded = content.count(INVALID_PLACEHOLDER)
        stats["mermaid"] += mermaid
        stats["bidfig"] += bidfig
        stats["degraded"] += degraded
        if mermaid or bidfig or degraded:
            nodes[str(item.get("node_id") or "")] = {"mermaid": mermaid, "bidfig": bidfig, "degraded": degraded}
    stats["nodes"] = nodes
    return stats


_BIDFIG_REPAIR_SYSTEM_PROMPT = (
    "你是图表数据修正器。给你若干不合法的 bidfig 图表 JSON 与具体报错，"
    "只输出修正后的 ```bidfig 围栏（围栏块数与输入一致、顺序一致），不要输出任何解释。"
    "修正时只改报错指出的字段，其余数据原样保留，严禁虚构新的任务、日期与数值。"
)

_BIDFIG_REPAIR_USER_TEMPLATE = """以下 __COUNT__ 块 bidfig 图表数据不合法：

__BLOCKS__

请输出修正后的 __COUNT__ 块 ```bidfig 围栏（每块格式：```bidfig 换行、一行修正后的 JSON、换行 ```），
顺序与上面一致，除围栏外不要输出任何文字。"""


async def tighten_bidfig_fences(llm, body: str) -> tuple[str, dict[str, int]]:
    """生成期收紧重渲（E2）：落盘前试渲染全部 bidfig 围栏，失败块带具体报错
    （如「phases 最多 5 个阶段列」）回炉一次 LLM 修正，仍不合法才降级为占位说明。

    必须在 generation worker 落盘前做——API 出口的 render_bidfig_fences 无法回炉。
    返回 (正文, {"repaired": n, "degraded": m})；无失败块原样返回（零额外 LLM 调用）。
    回炉输出围栏数对不上时按整体降级处理（宁降级不误替）。
    """
    stats = {"repaired": 0, "degraded": 0}
    failed = [fence for fence in bidfig_fence_payloads(body) if fence["error"]]
    if not failed:
        return body, stats
    blocks = "\n\n".join(
        "第 %d 块（```bidfig 围栏内 JSON 原文）：\n%s\n报错：%s" % (index + 1, fence["payload"], fence["error"])
        for index, fence in enumerate(failed)
    )
    repaired_payloads: list[Optional[str]] = [None] * len(failed)
    try:
        answer = strip_code_fence(
            await llm.generate(
                _BIDFIG_REPAIR_SYSTEM_PROMPT,
                _BIDFIG_REPAIR_USER_TEMPLATE.replace("__COUNT__", str(len(failed))).replace("__BLOCKS__", blocks),
            )
        )
        fixed_blocks = bidfig_fence_payloads(close_unterminated_bidfig_fence(answer))
        if len(fixed_blocks) == len(failed):
            for index, block in enumerate(fixed_blocks):
                if not block["error"]:
                    repaired_payloads[index] = block["payload"]
    except Exception:
        logger.warning("bidfig tighten repair llm call failed", exc_info=True)

    lines = body.split("\n")
    # 倒序替换防行号位移：修好的只换围栏内 JSON 行，仍坏的整块换占位说明
    for index in range(len(failed) - 1, -1, -1):
        fence = failed[index]
        payload = repaired_payloads[index]
        if payload is not None:
            lines[fence["start"] + 1: fence["end"]] = payload.split("\n")
            stats["repaired"] += 1
        else:
            lines[fence["start"]: fence["end"] + 1] = [INVALID_PLACEHOLDER]
            stats["degraded"] += 1
    return "\n".join(lines), stats


# ------------------------------------------------------------------ 生成要求（决策 38-41）


def normalize_generation_options(raw: Any) -> dict[str, Any] | None:
    """生成要求规范化：parts 去重保序且非空（空→仅技术）、word_count 越界回 None、charts 布尔、fig_mode 透传。

    返回 None 表示"未设置"（存量向导 / 老客户端载荷），下游一律按无约束处理（决策 42）。
    word_count=None 是允许的草稿态——必填校验在进入编写大纲时做（决策 40）。
    2026-10-23：产品要求只提供技术应标编写——parts 强制收敛为 ["technical"]
    （存量数据里已保存的 business 在读取/消费时一律被过滤；schema 侧同步收敛老客户端载荷）。
    """
    if not isinstance(raw, dict):
        return None
    parts: list[str] = []
    for item in raw.get("parts") or []:
        key = str(item or "").strip().lower()
        if key in GENERATION_PARTS and key in GENERATION_PARTS_ENABLED and key not in parts:
            parts.append(key)
    if not parts:
        parts = ["technical"]
    word_count: int | None = None
    raw_count = raw.get("word_count")
    if raw_count is not None and not isinstance(raw_count, bool):
        try:
            word_count = int(raw_count)
        except (TypeError, ValueError):
            word_count = None
    if word_count is not None and not (
        GENERATION_WORD_COUNT_MIN <= word_count <= GENERATION_WORD_COUNT_MAX
    ):
        word_count = None
    charts = raw.get("charts")
    fig_mode = str(raw.get("fig_mode") or "").strip().lower()
    if fig_mode not in GENERATION_FIG_MODES:
        fig_mode = "bidfig"
    return {
        "parts": parts,
        "word_count": word_count,
        "charts": True if charts is None else bool(charts),
        "fig_mode": fig_mode,
    }


def generation_options_of(requirements: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(requirements, dict):
        return None
    return normalize_generation_options(requirements.get("generation_options"))


def generation_parts_label(parts: list[str]) -> str:
    labels = [GENERATION_PART_LABELS.get(str(part), str(part)) for part in parts or []]
    return "+".join(labels) if labels else GENERATION_PART_LABELS["technical"]


def build_generation_constraints_text(options: dict[str, Any] | None) -> str:
    """Spec 生成/AI 修订 prompt 的「生成要求」块；未设置时明示无约束。"""
    if not options:
        return "无（按招标要素与编写需求自行规划）"
    lines: list[str] = []
    parts = [str(part) for part in options.get("parts") or ["technical"]]
    scope = generation_parts_label(parts)
    if len(parts) == 1 and parts[0] in _GENERATION_PART_EXCLUSIONS:
        lines.append(
            f"- 生成内容：仅{scope}——大纲只包含{scope}的章节，不得出现{_GENERATION_PART_EXCLUSIONS[parts[0]]}"
        )
    else:
        lines.append(f"- 生成内容：{scope}——大纲需分别覆盖商务与技术两部分的章节")
    word_count = options.get("word_count")
    if word_count:
        low = int(word_count * (1 - GENERATION_WORD_COUNT_TOLERANCE))
        high = int(word_count * (1 + GENERATION_WORD_COUNT_TOLERANCE))
        lines.append(
            f"- 目标总字数：约 {word_count:,} 字（允许 ±20%，即 {low:,}–{high:,} 字）——"
            "所有章节 article_count×text_count 之和必须落在该区间，据此规划小节数量与每章 article_count/text_count；"
            "字数约束不改变多级目录要求，不得为凑字数把大纲压成只有一级章节"
        )
    if options.get("charts", True):
        lines.append("- 图表：需要配图——charts 按内容需要规划（每章 0-3 项）")
    else:
        lines.append("- 图表：不配图——每个章节的 charts 必须为空数组 []，不要规划任何表格或 mermaid 图")
    return "\n".join(lines)


# 问卷/追问轮的检查范围（2026-10-07 需求：生成内容未含商务部分时，检查材料不检查商务材料）。
# 措辞面向"提问与素材检查"，与 Spec 侧 _GENERATION_PART_EXCLUSIONS 的"章节"措辞区分。
_QUESTIONNAIRE_PART_EXCLUSIONS = {
    "technical": "商务部分相关内容（如资质证明、业绩证明、财务报表、报价策略、商务条款响应/偏离）",
    "business": "技术部分相关内容（如技术方案、实施方案、质量保障、售后服务、技术人员配置）",
}


def build_questionnaire_scope_text(options: dict[str, Any] | None) -> str:
    """问卷首轮/追问轮 prompt 的「检查范围」块：生成内容只勾某部分时，检查只针对该部分。

    2026-10-23 商务屏蔽后 parts 恒为 ["technical"]（normalize_generation_options 收口），
    未保存生成选项时同样按仅技术标检查——否则首轮问卷仍会问商务问题、建议商务素材。
    """
    if options:
        parts = [str(part) for part in options.get("parts") or ["technical"]]
    else:
        parts = ["technical"]
    scope = generation_parts_label(parts)
    if len(parts) == 1 and parts[0] in _QUESTIONNAIRE_PART_EXCLUSIONS:
        excluded = _QUESTIONNAIRE_PART_EXCLUSIONS[parts[0]]
        return (
            f"- 检查范围：仅{scope}——只针对{scope}编写所需的信息提问与检查素材，"
            f"严禁提出或追问{excluded}的问题，也不要建议用户补充该类素材"
        )
    return f"- 检查范围：{scope}——按商务与技术两部分各自编写所需的信息提问与检查素材"


def apply_generation_options_to_spec(
    spec: list[dict[str, Any]], options: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """配图=否时强制清空 charts（决策 41）：AI 输出与手工保存都经此处，UI 隐藏入口不是唯一防线。"""
    if not options or options.get("charts", True):
        return spec
    return [{**node, "charts": None} for node in spec]


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


def is_flat_degraded_spec(spec: list[dict[str, Any]]) -> bool:
    """全部节点 level=1 且数量超过阈值 → 平级劣化；节点少的小纲要只有一级章节属正常。"""
    if len(spec) <= SPEC_FLAT_DEGRADATION_MIN_NODES:
        return False
    return all(int(node.get("level") or 1) == 1 for node in spec)


def build_chart_plan_text(node: dict[str, Any], *, charts_enabled: bool = True) -> str:
    if not charts_enabled:
        return "- 不配图（用户生成要求）：本章不要输出任何表格或 mermaid 图，全部用文字段落或列表表达"
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


def chapter_intro_plan(spec_scope: list[dict[str, Any]]) -> dict[str, list[str]]:
    """父节点同批含子节时只写导语：返回 {node_id: 同批直属子节标题列表}。

    node_id 由 normalize_outline 按层级分配（"2" / "2.1" / "10.4.1"），
    前缀即结构父子；key 命中的节点本轮改为章首导语，value 用于导语中预告结构。
    仅选中子节（未选父节）或仅选父节（无子节同批）都不受影响，仍走全量正文。
    """
    plan: dict[str, list[str]] = {}
    for node in spec_scope:
        node_id = str(node.get("node_id") or "")
        if not node_id:
            continue
        prefix = node_id + "."
        children = [
            str(other.get("title") or "")
            for other in spec_scope
            if str(other.get("node_id") or "").startswith(prefix)
            and "." not in str(other.get("node_id"))[len(prefix):]
        ]
        if children:
            plan[node_id] = children
    return plan


_HEADING_LINE_RE = re.compile(r"^\s*#{1,6}\s+\S")


def sanitize_chapter_intro(body: str) -> str:
    """导语兜底：模型越界输出小节标题时，截断到首个标题行之前。

    成稿的重复来自"章级正文自带编号小节"；导语被截断后最多损失越界部分，
    保底不空——开篇即标题时剥标题行取首个非空自然段，仍空则原样返回交由
    上层空结果校验兜错。
    """
    lines = (body or "").splitlines()
    cut = next((i for i, line in enumerate(lines) if _HEADING_LINE_RE.match(line)), None)
    if cut is None:
        return (body or "").strip()
    kept = "\n".join(lines[:cut]).strip()
    if kept:
        return kept
    first_para: list[str] = []
    for line in lines:
        if _HEADING_LINE_RE.match(line):
            continue
        if line.strip():
            first_para.append(line)
        elif first_para:
            break
    return "\n".join(first_para).strip() or (body or "").strip()


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

# 2026-10-23 商务应标已屏蔽（GENERATION_PARTS_ENABLED 只放行 technical）：
# 素材补充建议同样只面向技术标，禁止把商务证明类材料建议回来。
WIZARD_ANALYSIS_EXTRA_INSTRUCTION = (
    "\n\n另外，请在输出的 JSON 对象中增加 suggested_materials 字段："
    '[{"name": "建议投标人补充上传的素材名称", "reason": "对应哪条招标要求/评分标准"}]，'
    "最多 8 项。编写内容固定为技术应标部分（商务应标已关闭），"
    "只建议编写技术标真正需要、且投标公司通常需要另行准备的材料"
    "（如技术方案与实施方案素材、交付与验收计划、项目班子人员的技术职称/证书、"
    "检测报告、厂家技术资料或技术授权、拟引用的技术标准规范等）；"
    "严禁建议任何商务标材料——营业执照、资质荣誉证书、财务报表/审计报告、"
    "纳税与社保证明、业绩合同/业绩证明、报价与商务条款响应/偏离类材料一律不得出现。"
)

# prompt 之外的第二道防线（2026-09-12 暗标假阳性同款教训：指令约束不能是唯一防线）：
# 名称命中商务关键词的建议项直接丢弃，防止模型漏看指令把商务材料又建议回来。
_BUSINESS_MATERIAL_KEYWORDS = (
    "营业执照",
    "资质",
    "财务",
    "审计",
    "纳税",
    "社保",
    "业绩",
    "报价",
    "商务",
    "开户",
    "征信",
    "荣誉",
    "信用证明",
    "保函",
    "保证金",
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
        if any(keyword in name for keyword in _BUSINESS_MATERIAL_KEYWORDS):
            continue
        items.append({"name": name, "reason": str(entry.get("reason") or "").strip()[:200]})
    return items


# 问卷侧的商务问题硬过滤（2026-10-23 需求问答仍问出商务问题：prompt 排除不是唯一防线，
# 与素材建议侧同款教训）。只看 question/why 正文，不看 topic——topic 是粗标签，
# 「资质」话题下的 ISO9001 质量体系证书属技术标内容，误杀面太大。
# 宽泛词（资质/业绩/偏离/荣誉）带技术语境豁免：「项目班子人员的技术资质/业绩」属技术标信息；
# 素材侧无此豁免（素材名即商务材料名）。
_BUSINESS_QUESTION_HARD_KEYWORDS = (
    "营业执照",
    "财务",
    "审计",
    "纳税",
    "社保",
    "报价",
    "价格",
    "商务",
    "开户",
    "征信",
    "保函",
    "保证金",
    "信用证明",
)
_BUSINESS_QUESTION_BROAD_KEYWORDS = ("资质", "业绩", "偏离", "荣誉")
_TECHNICAL_CONTEXT_RE = re.compile(
    "技术|人员|班子|项目经理|实施|方案|售后|培训|检测|授权|标准|规范|工期|验收|职称|体系|认证"
)


def is_business_question(question: dict[str, Any]) -> bool:
    """问卷问题是否属商务标范畴：question/why 正文命中即判商务（topic 粗标签不参与）。"""
    for field in ("question", "why"):
        text = str(question.get(field) or "")
        if any(keyword in text for keyword in _BUSINESS_QUESTION_HARD_KEYWORDS):
            return True
        if any(keyword in text for keyword in _BUSINESS_QUESTION_BROAD_KEYWORDS) and not _TECHNICAL_CONTEXT_RE.search(
            text
        ):
            return True
    return False


def filter_business_questions(questionnaire: dict[str, Any] | None) -> dict[str, Any] | None:
    """剔除问卷里的商务问题（纯函数）：新问卷在 normalize 收口，存量问卷在出参/下游渲染处兜底。"""
    if not isinstance(questionnaire, dict) or not isinstance(questionnaire.get("questions"), list):
        return questionnaire
    kept = [q for q in questionnaire["questions"] if not (isinstance(q, dict) and is_business_question(q))]
    return {**questionnaire, "questions": kept}


# ---- JSON 交互容错（2026-09-15 首轮问卷「模型输出不是合法 JSON」事故） ----

# 与 Spec 平级重试同口径：首次耗时 × 系数 ≥ 超时预算即跳过重试，防同步微任务超时把首轮一起丢掉
JSON_RETRY_TIME_FACTOR = 2.2
JSON_RETRY_ADDENDUM = (
    "\n\n【重要】你上一次的输出不是合法 JSON，已被丢弃。请严格只输出一个合法 JSON："
    "不要任何前后说明文字、不要代码块标记；字符串内的英文双引号必须写成 \\\"；"
    "不要尾逗号；确保所有括号闭合；控制总长度，宁可少几项也不要输出到一半被截断。"
)
_UNPARSEABLE_LOG_SNIPPET = 400


def _json_start_candidates(value: str) -> list[int]:
    """首个 '{' 与首个 '[' 的位置（存在者）。两种起点都要试：模型在 JSON 前写了带方括号的
    说明（"参考[1]…"）时先出现的是垃圾 '['，Spec 顶层是数组时先出现的 '{' 只是首个元素。"""
    return [index for index in (value.find("{"), value.find("[")) if index >= 0]


def parse_json_payload_tolerant(text: str) -> Any | None:
    """V1 parse_json_payload 之上再补一层 raw_decode：忽略首个完整 JSON 值之后的尾随文字。

    V1 的括号切片取全文最后一个 '}'，模型在 JSON 后追加说明（含花括号/方括号）时会切进垃圾；
    raw_decode 只取从起点开始的第一个完整值。两种起点都试、取覆盖最长者（见 _json_start_candidates）。
    V1 文件保持零改动，这里包一层。
    """
    parsed = parse_json_payload(text)
    if parsed is not None:
        return parsed
    value = strip_code_fence((text or "").strip())
    decoder = json.JSONDecoder()
    best: tuple[int, Any] | None = None
    for start in _json_start_candidates(value):
        try:
            candidate, end = decoder.raw_decode(value, start)
        except ValueError:
            continue
        if isinstance(candidate, (dict, list)) and (best is None or end - start > best[0]):
            best = (end - start, candidate)
    return best[1] if best else None


def _salvage_from(value: str) -> str | None:
    """从 value[0]（须为 '{' 或 '['）起扫描，返回可被 json.loads 的最长完整前缀文本。"""
    pairs = {"{": "}", "[": "]"}
    stack: list[str] = []
    cut_points: list[tuple[int, list[str]]] = []
    in_string = False
    escaped = False
    for index, char in enumerate(value):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in pairs:
            stack.append(char)
        elif char in ("}", "]"):
            if not stack or pairs[stack[-1]] != char:
                break  # 结构已乱，后面不可信；仍可用此前记下的切点
            stack.pop()
            # 只在「根闭合」或「元素直接在数组里闭合」处记切点（数组栈深 ≤2：顶层数组 /
            # 顶层对象内的数组）；节点内部子容器（如 charts 数组）闭合不算，免得救出残缺节点
            if not stack or (len(stack) <= 2 and stack[-1] == "["):
                cut_points.append((index + 1, list(stack)))
    for end, snapshot in reversed(cut_points):
        candidate = value[:end].rstrip()
        if candidate.endswith(","):
            candidate = candidate[:-1]
        candidate += "".join(pairs[opener] for opener in reversed(snapshot))
        try:
            json.loads(candidate)
        except ValueError:
            continue
        return candidate
    return None


def salvage_truncated_json(text: str) -> Any | None:
    """救援被截断的 JSON：保留已完整闭合的元素、补齐未闭合括号。

    面向本模块的顶层形态——`{"questions": [ {…}, {…}, {"topic": "…`、`[ {…}, {…}, {"title": "…`
    与 `{"basic": {…}, "scoring": [ {…}, {…`（招标要素）。按字符扫描跟踪字符串/转义与括号栈，
    元素在栈深 ≤2 处闭合即记可切点；从最后一个可切点往前试：切掉未完成尾巴、去尾逗号、
    按栈补闭合。两种起点都试、取救出文本最长者；首个元素都没完整时返回 None。有损但优于整轮报错。
    """
    value = strip_code_fence((text or "").strip())
    best: str | None = None
    for start in _json_start_candidates(value):
        candidate = _salvage_from(value[start:])
        if candidate is not None and (best is None or len(candidate) > len(best)):
            best = candidate
    if best is None:
        return None
    parsed = json.loads(best)
    return parsed if isinstance(parsed, (dict, list)) else None


def _log_unparseable_llm_output(label: str, text: str, response: Any, elapsed: float) -> None:
    usage = getattr(response, "usage", None)
    completion_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None
    thinking = getattr(response, "thinking", None)
    stripped = strip_code_fence((text or "").strip())
    try:
        json.loads(stripped)
        decode_hint = "loads ok?!"
    except json.JSONDecodeError as exc:
        lo, hi = max(0, exc.pos - 120), min(len(stripped), exc.pos + 60)
        decode_hint = f"{exc.msg} at {exc.pos}/{len(stripped)} …{stripped[lo:hi]!r}…"
    except ValueError as exc:
        decode_hint = str(exc)
    logger.warning(
        "bid_wizard llm output not JSON (%s): %.1fs len=%d completion_tokens=%s thinking_len=%s "
        "fence=%s decode=%s HEAD=%r TAIL=%r",
        label,
        elapsed,
        len(text),
        completion_tokens,
        len(thinking) if isinstance(thinking, str) else None,
        text.lstrip().startswith("```"),
        decode_hint,
        text[:_UNPARSEABLE_LOG_SNIPPET],
        text[-_UNPARSEABLE_LOG_SNIPPET:],
    )


class WizardLLM:
    """Thin instrumented client wrapper shared by sync and celery flows."""

    def __init__(self, *, timeout: float = 180.0) -> None:
        self.timeout = timeout
        self.client = instrument_llm_client(create_llm_client(timeout=timeout))

    async def _generate_response(self, system_prompt: str, user_prompt: str) -> Any:
        from mini_agent.schema import Message

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        return await self.client.generate(messages=messages)

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._generate_response(system_prompt, user_prompt)
        return str(getattr(response, "content", "") or "").strip()

    async def generate_json(self, system_prompt: str, user_prompt: str) -> Any:
        """JSON 交互：解析失败先重试一次，再救援截断片段，最后才向用户报错。

        模型输出偶发不可解析（同一输入复跑通常正常，2026-09-15 首轮问卷实证），
        以前这里直接 RuntimeError 且原文不留痕，用户只能手动重试。现在：
        ① 首次不可解析 → WARNING 记录长度/首尾/usage（下次事故可定因）；
        ② 时间预算允许（首次耗时 × JSON_RETRY_TIME_FACTOR < timeout）→ 带纠正语重试一次；
        ③ 仍不可解析 → salvage_truncated_json 救援截断输出（保留已完整的元素）；
        ④ 都不行才抛「模型输出不是合法 JSON，请重试」。
        """
        started = time.monotonic()
        first = await self._generate_response(system_prompt, user_prompt)
        first_text = str(getattr(first, "content", "") or "").strip()
        parsed = parse_json_payload_tolerant(first_text)
        if parsed is not None:
            return parsed
        elapsed = time.monotonic() - started
        _log_unparseable_llm_output("first", first_text, first, elapsed)

        retry_text = ""
        if elapsed * JSON_RETRY_TIME_FACTOR < self.timeout:
            retry_started = time.monotonic()
            retry = await self._generate_response(system_prompt, user_prompt + JSON_RETRY_ADDENDUM)
            retry_text = str(getattr(retry, "content", "") or "").strip()
            parsed = parse_json_payload_tolerant(retry_text)
            if parsed is not None:
                logger.info(
                    "bid_wizard llm json retry succeeded: retry=%.1fs first_len=%d retry_len=%d",
                    time.monotonic() - retry_started, len(first_text), len(retry_text),
                )
                return parsed
            _log_unparseable_llm_output("retry", retry_text, retry, time.monotonic() - retry_started)
        else:
            logger.warning(
                "bid_wizard llm json retry skipped: first call %.1fs × %.1f ≥ timeout %.0fs",
                elapsed, JSON_RETRY_TIME_FACTOR, self.timeout,
            )

        for label, text in (("retry", retry_text), ("first", first_text)):
            salvaged = salvage_truncated_json(text)
            if salvaged is not None:
                logger.warning(
                    "bid_wizard llm json salvaged from %s output (len=%d): kept a complete prefix",
                    label, len(text),
                )
                return salvaged
        raise RuntimeError("模型输出不是合法 JSON，请重试")


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
    llm: WizardLLM,
    *,
    analysis: dict[str, Any],
    material_index_text: str,
    generation_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user_prompt = (
        QUESTIONNAIRE_USER_TEMPLATE.replace(
            "__ANALYSIS_JSON__", json.dumps(analysis, ensure_ascii=False)[:ANALYSIS_JSON_MAX_CHARS]
        )
        .replace("__MATERIAL_INDEX__", material_index_text or "（无素材）")
        .replace("__SCOPE_CONSTRAINTS__", build_questionnaire_scope_text(generation_options))
    )
    payload = await llm.generate_json(QUESTIONNAIRE_SYSTEM_PROMPT, user_prompt)
    questionnaire = normalize_questionnaire(payload)
    if not questionnaire["questions"]:
        raise RuntimeError("问卷生成结果为空，请重试")
    return questionnaire


ROUND_SYSTEM_PROMPT = (
    "你是资深投标文件专家，正在主持一场针对投标编写需求的多轮追问（grill-me 式）："
    "用户刚补充了新素材或完成了作答。由你判断是否需要新一轮提问："
    "只问当前仍缺失或会实质影响标书质量、必须确认的信息；若已确认的需求加上素材足以支撑编写，"
    "就返回空列表表示追问可以结束。"
    "本次投标编写仅面向技术应标部分（商务应标已关闭）：严禁追问资质证书、业绩证明、"
    "财务报表、纳税社保证明、报价与商务条款等商务内容。"
    "只输出一个合法 JSON 对象，不要输出任何其他文字或代码块标记。"
)

ROUND_USER_TEMPLATE = """## 招标要素
__ANALYSIS_JSON__

## 当前编写需求（已确认问答；[已补充素材] 表示用户刚为此问题补充了新素材）
__REQUIREMENTS_TEXT__

## 检查范围（用户设定的生成内容，优先级高于下方通用规则）
__SCOPE_CONSTRAINTS__

## 公司素材索引（编号｜标题｜摘要｜关键词；已包含新补充素材）
__MATERIAL_INDEX__

## 已问过的问题（不要重复提问）
__EXISTING_QUESTIONS__

请输出新一轮问题 JSON（若判断无需追问，输出 {"questions": []}）：

{"questions": [
  {"topic": "主题（技术/人员/交付/其他）",
   "question": "向投标人提出的问题，一句话",
   "why": "为什么需要这个信息（与招标要求的关联）",
   "suggested_answer": "建议答案：优先采用素材索引中的真实事实（特别是新补充的素材），注明来源；素材中没有则留空字符串",
   "source": "素材依据（如 '素材名#3'），推断时填 '推断'",
   "inferred": false}
]}

规则：
1. 是否追问由你判断：已确认的需求加上素材足以支撑标书编写时，返回空列表，不要为了凑数提问；
2. 需要追问时输出 1-8 题，且都必须落在「检查范围」内，聚焦两类：标记[已补充素材]的问题若新素材已能回答，改问确认式问题（如「证书编号是否为 XX、有效期至 XX？」）并把素材中提取的具体事实写入 suggested_answer 与 source；
3. 新素材仍回答不了的，换更具体的角度继续追问，不要原样重复已问过的问题；
4. 已回答、已采纳、与素材无关或超出「检查范围」的问题不再问；
5. 素材没有依据的建议答案必须 inferred=true；严禁在答案里虚构公司资质、业绩、人员与数据。"""


def render_existing_questions_text(questionnaire: dict[str, Any] | None) -> str:
    """Render 已有问题清单（含作答状态）供新一轮 prompt 去重。"""
    action_labels = {
        "answered": "已自定义回答",
        "adopted": "已采纳建议",
        "skipped": "已跳过",
        "supplemented": "已补充素材",
    }
    lines: list[str] = []
    for question in (questionnaire or {}).get("questions") or []:
        if not isinstance(question, dict):
            continue
        label = action_labels.get(str(question.get("action") or ""), "未回答")
        text = str(question.get("question") or "")[:200]
        lines.append(f"- [{question.get('topic') or '其他'}] {text}（{label}）")
    return "\n".join(lines)[:SPEC_CONTEXT_MAX_CHARS]


async def generate_questionnaire_round(
    llm: WizardLLM,
    *,
    analysis: dict[str, Any],
    requirements_text: str,
    material_index_text: str,
    existing_questions_text: str,
    generation_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """再次检查/自动追问（多轮）：AI 判断是否需要新一轮；空列表=需求已充分、追问结束。"""
    user_prompt = (
        ROUND_USER_TEMPLATE.replace(
            "__ANALYSIS_JSON__", json.dumps(analysis, ensure_ascii=False)[:ANALYSIS_JSON_MAX_CHARS]
        )
        .replace("__REQUIREMENTS_TEXT__", (requirements_text or "无")[:SPEC_CONTEXT_MAX_CHARS])
        .replace("__SCOPE_CONSTRAINTS__", build_questionnaire_scope_text(generation_options))
        .replace("__MATERIAL_INDEX__", material_index_text or "（无素材）")
        .replace("__EXISTING_QUESTIONS__", existing_questions_text or "（无）")
    )
    payload = await llm.generate_json(ROUND_SYSTEM_PROMPT, user_prompt)
    return normalize_questionnaire(payload)


async def _generate_spec_once(llm: WizardLLM, user_prompt: str) -> list[dict[str, Any]]:
    payload = await llm.generate_json(SPEC_SYSTEM_PROMPT, user_prompt)
    nodes = payload if isinstance(payload, list) else payload.get("outline") if isinstance(payload, dict) else None
    return normalize_spec(nodes if isinstance(nodes, list) else [])


async def generate_spec(
    llm: WizardLLM,
    *,
    analysis: dict[str, Any],
    requirements_text: str,
    material_index_text: str,
    generation_options: dict[str, Any] | None = None,
    time_budget_seconds: float | None = None,
    toc_reference_text: str = "",
) -> list[dict[str, Any]]:
    """Spec 生成；首轮平级劣化（见 is_flat_degraded_spec）且时间预算够时带纠正指令重试一次。

    重试结果只有真正多级时才替换首轮；重试仍平级或抛错都保留首轮可用结果（用户可手工/AI 修订）。
    toc_reference_text（23 号 O7）：历史标书目录参照块（build_toc_reference_block 产物），
    为空时占位替换为空串、模板行为与存量一致；注入后 is_flat_degraded_spec 劣化判据照常生效。
    """
    user_prompt = (
        SPEC_USER_TEMPLATE.replace(
            "__ANALYSIS_JSON__", json.dumps(analysis, ensure_ascii=False)[:ANALYSIS_JSON_MAX_CHARS]
        )
        .replace("__REQUIREMENTS_TEXT__", (requirements_text or "无")[:SPEC_CONTEXT_MAX_CHARS])
        .replace("__GENERATION_CONSTRAINTS__", build_generation_constraints_text(generation_options))
        .replace("__MATERIAL_INDEX__", material_index_text or "（无素材）")
        .replace("__TOC_REFERENCE__", toc_reference_text or "")
        .replace("__MAX_NODES__", str(OUTLINE_MAX_NODES))
    )
    started = time.monotonic()
    spec = await _generate_spec_once(llm, user_prompt)
    if not spec:
        raise RuntimeError("Spec 生成结果为空，请重试")
    if is_flat_degraded_spec(spec):
        elapsed = time.monotonic() - started
        if time_budget_seconds is not None and elapsed * _SPEC_RETRY_BUDGET_FACTOR >= time_budget_seconds:
            logger.warning(
                "bid-wizard spec is flat (%d nodes, all level=1) but first pass used %.0fs of %.0fs budget; skip retry",
                len(spec), elapsed, time_budget_seconds,
            )
        else:
            logger.warning("bid-wizard spec is flat (%d nodes, all level=1); retrying once with correction", len(spec))
            try:
                retried = await _generate_spec_once(
                    llm, user_prompt + SPEC_FLAT_RETRY_ADDENDUM.replace("__FLAT_COUNT__", str(len(spec)))
                )
            except Exception as exc:  # 重试失败不能丢掉首轮可用结果
                logger.warning("bid-wizard spec flat-retry failed, keeping first pass: %s", exc)
                retried = []
            if retried and not is_flat_degraded_spec(retried):
                spec = retried
            elif retried:
                logger.warning("bid-wizard spec flat-retry still flat (%d nodes); keeping first pass", len(retried))
    # 评分覆盖重试（2026-10-25 复盘轮）：技术类评分项零响应时点名漏项重试一次
    # （confirm 侧还有硬门禁兜底；此处先自动补救，多数情况用户无感）。重试更优才替换。
    scoring_items = scoring_items_of(analysis)
    if scoring_items:
        uncovered = gate_blocking_scoring_items(spec, analysis)
        if uncovered:
            elapsed = time.monotonic() - started
            if time_budget_seconds is not None and elapsed * _SPEC_RETRY_BUDGET_FACTOR >= time_budget_seconds:
                logger.warning(
                    "bid-wizard spec uncovered scoring items (%s) but first pass used %.0fs of %.0fs budget; skip retry",
                    "、".join(item["title"] for item in uncovered)[:120], elapsed, time_budget_seconds,
                )
            else:
                logger.warning(
                    "bid-wizard spec misses scoring coverage (%s); retrying once",
                    "、".join(item["title"] for item in uncovered)[:120],
                )
                items_text = "\n".join(
                    "- %s（%s）：%s" % (item["title"], item.get("weight") or "?", (item.get("criteria") or "")[:120])
                    for item in uncovered[:10]
                )
                try:
                    retried = await _generate_spec_once(
                        llm, user_prompt + _SPEC_COVERAGE_RETRY_ADDENDUM.replace("__UNCOVERED_ITEMS__", items_text)
                    )
                except Exception as exc:
                    logger.warning("bid-wizard spec coverage-retry failed, keeping first pass: %s", exc)
                    retried = []
                if retried and len(gate_blocking_scoring_items(retried, analysis)) < len(uncovered):
                    spec = retried
                elif retried:
                    logger.warning(
                        "bid-wizard spec coverage-retry not better (%d -> %d uncovered); keeping first pass",
                        len(uncovered), len(gate_blocking_scoring_items(retried, analysis)),
                    )
    return apply_generation_options_to_spec(spec, generation_options)


async def revise_spec(
    llm: WizardLLM,
    *,
    current_spec: list[dict[str, Any]],
    instruction: str,
    generation_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    user_prompt = (
        REVISE_USER_TEMPLATE.replace(
            "__CURRENT_SPEC__", json.dumps(current_spec, ensure_ascii=False)[:SPEC_CONTEXT_MAX_CHARS]
        )
        .replace("__GENERATION_CONSTRAINTS__", build_generation_constraints_text(generation_options))
        .replace("__INSTRUCTION__", (instruction or "").strip()[:2_000])
    )
    payload = await llm.generate_json(REVISE_SYSTEM_PROMPT, user_prompt)
    nodes = payload if isinstance(payload, list) else payload.get("outline") if isinstance(payload, dict) else None
    spec = normalize_spec(nodes if isinstance(nodes, list) else [])
    if not spec:
        raise RuntimeError("Spec 修订结果为空，请重试")
    return apply_generation_options_to_spec(spec, generation_options)


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
        "素材与需求未覆盖的部分如实说明并给出建议写法。"
        "本工具仅编写技术应标部分（商务应标已关闭）：涉及商务标内容（资质证书、业绩证明、"
        "财务报表、报价、商务条款等）的提问，如实说明该部分不在工具生成范围内、建议用户线下编制，"
        "不要代为展开商务内容。回答用中文，500 字以内，不要编造事实。"
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


# ------------------------------------------------------- material index io


def material_dir(workspace_path: Path, wizard_id: str, document_id: str) -> Path:
    return Path(workspace_path) / "bid-wizard" / wizard_id / "materials" / document_id


def write_material_index(
    workspace_path: Path,
    wizard_id: str,
    document_id: str,
    chunks: list[dict[str, Any]],
    metas: dict[int, dict[str, Any]],
    toc_text: str = "",
) -> None:
    """索引落盘；toc_text 非空（历史模板素材，O7）时在 index.md 顶部写「目录结构」小节。"""
    base = material_dir(workspace_path, wizard_id, document_id)
    chunk_dir = base / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for chunk in chunks:
        path = chunk_dir / f"{int(chunk['no']):03d}.md"
        path.write_text(render_chunk_file(chunk, metas.get(int(chunk["no"]), {})), encoding="utf-8")
    index_md = render_index_markdown(chunks, metas)
    if toc_text.strip():
        index_md = f"{TOC_REFERENCE_HEADING}\n{toc_text.strip()}\n\n{index_md}"
    (base / "index.md").write_text(index_md, encoding="utf-8")


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
        self._wizard_id = ""  # run() 加载上下文后覆写；提前调用生成辅助方法时不至于炸属性

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
        charts_enabled: bool = True,
        intro_children: Optional[list[str]] = None,
        fig_mode: str = "mermaid",
        score_items_text: str = "",
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
        if intro_children is not None:
            # 父节点与子节同批：只写章首导语，子节标题交给导语预告、正文由子节点自己生成
            user_prompt = (
                WIZARD_CHAPTER_INTRO_USER_TEMPLATE.replace("__ANALYSIS_JSON__", analysis_json)
                .replace("__REQUIREMENTS_TEXT__", requirements_text or "无")
                .replace("__OUTLINE_TEXT__", _outline_text(spec))
                .replace("__TITLE__", node["title"])
                .replace("__SUMMARY__", str(node.get("summary") or "（无摘要，按标题与大纲撰写）"))
                .replace("__SCORE_ITEMS_TEXT__", score_items_text or "（无直接对应的评分项）")
                .replace(
                    "__CHILD_TITLES__",
                    "\n".join(f"- {title}" for title in intro_children) or "（无）",
                )
                .replace("__PREV_SECTIONS__", prev_text)
                .replace("__ARTICLE_COUNT__", str(min(int(node.get("article_count") or 2), 2)))
                .replace("__TEXT_COUNT__", str(min(int(node.get("text_count") or 400), 400)))
            )
            body = strip_code_fence(
                await self.llm.generate(_WIZARD_CHAPTER_INTRO_SYSTEM_PROMPT, user_prompt)
            )
            body = sanitize_chapter_intro(body)
        else:
            user_prompt = (
                WIZARD_SECTION_USER_TEMPLATE.replace("__ANALYSIS_JSON__", analysis_json)
                .replace("__REQUIREMENTS_TEXT__", requirements_text or "无")
                .replace("__OUTLINE_TEXT__", _outline_text(spec))
                .replace("__TITLE__", node["title"])
                .replace("__SUMMARY__", str(node.get("summary") or "（无摘要，按标题与大纲撰写）"))
                .replace("__SCORE_ITEMS_TEXT__", score_items_text or "（无直接对应的评分项——按大纲摘要与编写需求撰写）")
                .replace(
                    "__CHART_PLAN_TEXT__", build_chart_plan_text(node, charts_enabled=charts_enabled)
                )
                .replace("__MATERIAL_CHUNKS__", material_chunks)
                .replace("__PREV_SECTIONS__", prev_text)
                .replace("__ARTICLE_COUNT__", str(node.get("article_count") or 2))
                .replace("__TEXT_COUNT__", str(node.get("text_count") or 400))
                .replace(
                    "__WORD_BUDGET__",
                    str((int(node.get("article_count") or 2)) * (int(node.get("text_count") or 400))),
                )
            )
            body = strip_code_fence(
                await self.llm.generate(
                    section_system_prompt(charts_enabled=charts_enabled, fig_mode=fig_mode),
                    user_prompt,
                )
            )
        if not body:
            raise RuntimeError(f"章节「{node['title']}」生成结果为空")
        body = clamp_mermaid_blocks(close_unterminated_mermaid_fence(body))
        fig_repaired = 0
        if fig_mode == "bidfig" and intro_children is None:
            body = clamp_bidfig_blocks(close_unterminated_bidfig_fence(body))
            # 生成期收紧重渲（E2）：落盘前试渲染，失败块带具体报错回炉一次 LLM 修正，仍败降级占位
            body, fig_repair = await tighten_bidfig_fences(self.llm, body)
            fig_repaired = int(fig_repair["repaired"])
            if fig_repair["repaired"] or fig_repair["degraded"]:
                logger.info(
                    "bidfig tighten: task=%s node=%s repaired=%d degraded=%d",
                    self.task_id,
                    node["node_id"],
                    fig_repair["repaired"],
                    fig_repair["degraded"],
                )
        # 拼装时会拼接标题行，剥掉正文开头模型自己重复输出的同名标题（防成稿双标题，
        # 也避免机检 duplicate_heading/empty_section 假信号——2026-10-23 预发布实测 60 节出现 3 处）
        body = strip_leading_title_headings(body, str(node.get("title") or ""))
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
        return {
            "node_id": node["node_id"],
            "title": node["title"],
            "word_count": word_count,
            # 供 run() 末尾全稿机检/覆盖矩阵就地消费（不落库）
            "content": content,
            "article_count": int(node.get("article_count") or 2),
            "text_count": int(node.get("text_count") or 400),
            # 图表质量机制观测面（2026-10-25）：本节回炉修正块数，run() 汇总进 fig_stats.repaired
            "fig_repaired": fig_repaired,
        }

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
        # 生成要求「不配图」（决策 41）贯穿到章节正文：system prompt 与图表计划文本都切无图版
        generation_options = generation_options_of(context["requirements"])
        charts_enabled = bool(generation_options.get("charts", True)) if generation_options else True
        fig_mode = str(generation_options.get("fig_mode") or "bidfig") if generation_options else "bidfig"
        prev_sections = await self._load_prev_sections(wizard.id, spec)
        intro_plan = chapter_intro_plan(spec_scope)
        # 评分台账（23 号 O2）：旧向导无 scoring_items 时全链路自然跳过
        scoring_items = scoring_items_of(context["analysis"])

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
                    charts_enabled=charts_enabled,
                    intro_children=intro_plan.get(node["node_id"]),
                    fig_mode=fig_mode,
                    score_items_text=render_score_items_text(
                        score_items_for_node(spec, node, scoring_items)
                    ),
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
        # 质检自动修复闭环（2026-10-25 用户反馈：不合格项直接甩给用户，用户无从下手）：
        # 机检 error 自动修订+复检直至清零（复用审读轮修订机制，计费经 UsageContext 同任务结算），
        # summary.doc_checks 呈现最终合格态、auto_fix 记录修复过程——机检从「只报告」升级为「报告即修复」
        checks: list[dict[str, str]] = []
        auto_fix_info: dict[str, Any] = {}
        try:
            fixer = BidWizardReviewAgent(
                task_id=self.task_id,
                workspace_dir=self.workspace_dir,
                session_factory=self.session_factory,
                event_callback=self.event_callback,
                cancel_event=self.cancel_event,
            )
            done, checks, auto_fix_info = await fixer.auto_fix_machine_errors(
                spec=spec,
                requirements=context["requirements"],
                analysis=context["analysis"],
                sections=done,
            )
        except BidWizardCancelled:
            raise
        except Exception:
            logger.exception("bid-wizard draft_checks/auto-fix failed: task=%s", self.task_id)
        summary: dict[str, Any] = {
            "section_total": len(spec_scope),
            "section_generated": len(done),
            "section_failed": len(failed),
            "word_count": sum(int(item.get("word_count") or 0) for item in done),
            "auto_fix": auto_fix_info,
        }
        summary["doc_checks"] = checks
        # 得分点覆盖矩阵（23 号 O2，S9 判定点核查）：无评分台账时为空列表（前端隐藏卡片）
        try:
            summary["score_coverage"] = score_coverage_matrix(
                spec, [item["node_id"] for item in done], scoring_items
            )
        except Exception:
            logger.exception("bid-wizard score_coverage failed: task=%s", self.task_id)
        # 全稿图表统计（23 号批次五 E1 观察）：mermaid 混出占比/降级块数供前端与 E2E 复算；
        # repaired＝生成期回炉修正块数（2026-10-25 补记——此前只记日志，summary 无法区分
        # 「全部一次通过」与「回炉后通过」，图片质量机制缺观测面）
        try:
            summary["fig_stats"] = fig_stats_of(done, fig_mode)
            summary["fig_stats"]["repaired"] = sum(
                int(item.get("fig_repaired") or 0) for item in done
            )
        except Exception:
            logger.exception("bid-wizard fig_stats failed: task=%s", self.task_id)
        return summary


def _safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.\-]", "_", value) or "section"


# ------------------------------------------------------------------ 审读-修订轮（23 号 O5：S5 Reader→Revise 一轮封顶）

# 审读按章分批（控制单次 prompt 体量）；修订只处理 high/medium，low 只进报告
REVIEW_BATCH_CHAPTERS = 4
REVIEW_BATCH_MAX_CHARS = 14_000
REVIEW_CHAPTER_MAX_CHARS = 6_000
REVIEW_REVISE_INPUT_MAX_CHARS = 12_000
REVIEW_MAX_REVISIONS = 20
REVIEW_FINDINGS_MAX = 60

# 质检自动修复闭环（2026-10-25 用户反馈：质检报告把「需处理」项直接甩给用户，用户无从下手）：
# 撰写/审读完成后的机检 error 不再只报告——复用修订机制自动改写涉事章并复检，直至 error
# 清零或轮次用尽；summary.doc_checks 只呈现最终（合格）状态，auto_fix 字段记录修复过程。
# 应急开关 BID_WIZARD_AUTO_FIX_ENABLED=0 回到「只报告」旧行为。
# 2026-10-25 复盘轮扩展：①图表编号重号 → 先跑确定性重编号（零 LLM）再复检；
# ②规划图缺失/禁用词残留升级 error 后一并自动修复；③超稿压缩（软预算收敛）。
AUTO_FIX_MAX_ROUNDS = 3
AUTO_FIX_MAX_NODES_PER_ROUND = 12
# 超稿自动压缩阈值：叶子节实际字数 > 规划×该比例 且 超 600 字才压缩（写作 prompt 已
# 硬化预算，这里只兜最差节）。BID_WIZARD_AUTO_FIX_OVERLONG_RATIO=0 关闭压缩。
AUTO_FIX_OVERLONG_RATIO = 1.6
AUTO_FIX_OVERLONG_MIN_EXCESS = 600
# error 类型 → 修订指令补充（detail 已描述问题，hint 告诉修订代理怎么改）
AUTO_FIX_TYPE_HINTS = {
    "missing_content": "本章正文为空——依据本章摘要、大纲与素材摘录完整撰写本章正文",
    "paired_duplication": (
        "删除本章正文中与直属子节同名的标题行及其重复内容：父章正文保持章首导语形态"
        "（1-2 个自然段，只概述本章定位与各小节安排），子节正文由系统另行生成、不得并入本章"
    ),
    "invalid_outline_ref": "把该引用改为大纲中实际存在的章节编号，或删除该引用",
    "chart_placeholder": (
        "删除图表（表格单元格/图内节点标签）中的「（请补充）」占位：能确定建议值的写建议值"
        "（可注明按采购人环境调整），确属待投标人补充的移出图表、在邻近正文段落说明"
    ),
    "caption_duplication": (
        "全稿图表编号必须唯一且按章顺序：把本章与他章冲突的图表题注改为本章未被占用的编号"
        "（章内按出现顺序 X-1、X-2…），并同步更新本章正文对该图表的引用"
    ),
    "planned_chart_missing": (
        "按本章图表计划补出缺失的图（类型与标题按计划执行，图表数据与本章正文一致、"
        "严禁虚构），补图不得删除既有内容"
    ),
    "forbidden_word": (
        "把口径卡禁用词改写为方案交付表述（如「唯一」按语境改「专属/独立承担」，"
        "「绝对」改「刚性/硬性」）；技术术语（唯一键/唯一标识/绝对时间等）不需要改"
    ),
}


def _auto_fix_enabled() -> bool:
    return os.environ.get("BID_WIZARD_AUTO_FIX_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _auto_fix_overlong_ratio() -> float:
    raw = os.environ.get("BID_WIZARD_AUTO_FIX_OVERLONG_RATIO", "").strip()
    if not raw:
        return AUTO_FIX_OVERLONG_RATIO
    try:
        value = float(raw)
    except ValueError:
        return AUTO_FIX_OVERLONG_RATIO
    return value if value > 0 else 0.0

REVIEW_FINDINGS_SYSTEM_PROMPT = (
    "你是资深投标文件审读专家（Reader）。逐章审读投标文件章节，找出影响得分的实质问题。"
    "只输出一个合法 JSON 对象，不要输出任何其他文字或代码块标记。"
)

REVIEW_FINDINGS_USER_TEMPLATE = """## 投标文件大纲（整体结构）
__OUTLINE_TEXT__

## 编写需求（含应答口径与口径卡）
__REQUIREMENTS_TEXT__

## 招标要求原文（工期/数值/承诺类口径以此为准；对承诺数值提意见前先与此比对）
__TENDER_REQ_TEXT__

## 招标★（废标/否决）条款清单（条款号与要点，★响应表/风险响应引用其编号）
__REJECTION_ITEMS_TEXT__

## 本次审读章节
__CHAPTERS__

请逐章审读，输出问题清单 JSON（没有实质问题就给空数组；不凑数、不提泛泛的润色建议）：

{"findings": [
  {"node_id": "章节号（必须是本次审读章节之一）", "severity": "high|medium|low",
   "where": "问题位置（如「第 2 段」「表格 X」「图 X」）",
   "issue": "问题描述与对得分的影响，一两句话"}
]}

审读口径：
1. high=评标专家会直接扣分甚至废标的硬伤：与招标要求/口径矛盾、承诺冲突、疑似虚构、关键要求漏响应；
   编写需求中的能力三态（口径-现有已实现/本项目建设/待确认）是硬口径——正文与三态矛盾（如 build 态
   写成「均已具备、可现场演示」）按 high 报，修订意见也严禁要求向相反方向改；
2. medium=明显质量问题：与前后章脱节或重复、图表与正文不符、流程图/示意图的节点与流向须与正文机制
   描述一致（如逐阶段进度推送不得画成末端单一环节）、统一称谓混用、（请补充）该确认却未确认
   （含图表内占位：mermaid 节点标签/表格单元格里的「请补充」应改为建议值或移出图表——同主题他章已给
   出的建议值可直接复用）、★响应表/需求对照表的编号与口径列素材无实际条款号时用条款功能名标注
   （如「★（招标文件上传能力）」）替代裸（请补充）、confirm 态条目写「本项目建设内容（具体规则需求
   确认阶段固化）」并保留交付时限锚点、把新数据对象/功能划归其他模块的扩写与编写需求和大纲的模块
   边界不一致、自曝内部实现短板或运维细节的表述（如「该节点内存偏紧」「任务路由漏配会滞留」「某工具
   不可靠不作为唯一依赖」「计费口径/计费状态」「插件发版」等内部运营/研发用语——私有化交付场景无对外
   计费与发版概念，应改为面向交付的能力与保障表述）、同一技术机制在不同章的主次口径不一致
   （如同一解析链路两章各认不同主引擎）、
   套话/模板腔成片（「AI 味」浓、整段无实质信息量）或残留思考过程/写作说明/对话语气
   （「好的，」「以下是」「接下来我们」「本章将介绍」类表述）——修订时按措辞铁律重写该类段落；
3. low=可改可不改的建议；
4. 只报章节内能确认的问题，严禁虚构章节里不存在的内容；每章最多 3 条，抓大放小；
5. 父章（其下有直属小节的章节）正文按全稿约定只是章首导语：导语作结构性概述不是内容空洞，
   不要以「本章单薄/应展开」为由给父章提扩写意见；父章若被展开成与小节重复的全章正文，
   反而应报 high（与子节成对重复）；
6. 口径/指标类发现须全局归并：同一主题（同一组验收指标数值、同一部署/算力/数据边界口径、
   同一交付承诺）在多章表述不一致或互相矛盾时，归并为一条 high，node_id 填最先出现该主题的章节，
   issue 中写明全稿应统一成的口径结论与依据；严禁对同一主题在不同章各提一条方向相反的意见
   （如一处要求补承诺、另一处要求删数值）——修订代理将按归并结论跨章对齐。
   对工期/交付/验收等承诺数值提意见前，先比对上方「招标要求原文」——招标文件明文取值是硬要求，
   严禁建议降格为「我方建议工期/以双方确认为准」类弱化表述；意见中「招标文件只明确/未明确某值」
   类事实断言必须能在招标要求原文或编写需求中找到出处（写明出处），找不到出处就不要下该断言；
   归并意见要求多章同步时，在 issue 中点名关联章节号（如「并见 1.1、1.3」），系统会同步下发。"""

REVIEW_REVISE_SYSTEM_PROMPT = (
    "你是资深投标文件编写专家（Revise）。按审读意见修订指定章节：保留既有正确内容与结构，"
    "只修审读意见指出的问题处，不做无关扩写；输出修订后的完整章节正文（Markdown，"
    "不含本节标题，系统会自动添加；不要输出解释或前言）。"
    "公司资质、业绩、项目、人员与数据必须与原文/素材一致，不得新增素材之外的事实；"
    "素材与需求都未覆盖的事实性内容用“（请补充）”占位。"
    "涉及验收指标数值、部署/算力/数据边界、交付承诺等全稿性口径时，"
    "须与「全稿口径基准」保持一致，不得本章另立一套说法；基准互相矛盾时以编写需求/招标要求为准并采用一致表述。"
    "编写需求中的能力三态是硬口径：build/confirm 态能力严禁改写为「已具备/已实现/可现场演示」等现有能力表述，"
    "删除（请补充）占位说明时必须保留对应三态口径措辞（本项目建设内容/需求确认阶段固化），宁可留占位不可虚构已实现。"
    "审读意见与招标要求原文或编写需求冲突时，以后者为准：不得执行把招标明文工期/承诺数值"
    "降格为「我方建议值/以双方确认为准」的意见，该类意见按招标原文取值改回正面承诺。"
    + _WIZARD_WORDING_RULES
)

REVIEW_REVISE_USER_TEMPLATE = """## 投标文件大纲（整体结构）
__OUTLINE_TEXT__

## 编写需求（含应答口径与口径卡）
__REQUIREMENTS_TEXT__

## 招标要求原文（工期/数值/承诺类口径以此为准，不得凭印象改写）
__TENDER_REQ_TEXT__

## 招标★（废标/否决）条款清单（条款号与要点，★响应表/风险响应引用其编号）
__REJECTION_ITEMS_TEXT__

## 全稿口径基准（各章审读结论中涉及指标/口径/承诺的 high 发现，本章涉及同一主题时须与之一致、不得冲突）
__GLOBAL_FINDINGS_TEXT__

## 本章响应的评分项
__SCORE_ITEMS_TEXT__

## 待修订章节（当前正文）
__CURRENT_CONTENT__

## 审读意见（按此修订，改完的问题不要再出现）
__FINDINGS_TEXT__

请输出修订后的完整章节正文（Markdown，不含本节标题）。"""


def batch_review_chapters(
    chapters: list[dict[str, Any]],
    *,
    max_chapters: int = REVIEW_BATCH_CHAPTERS,
    max_chars: int = REVIEW_BATCH_MAX_CHARS,
) -> list[list[dict[str, Any]]]:
    """审读分批（纯函数）：按大纲序每批 ≤max_chapters 章且正文合计 ≤max_chars。

    单章超限也独立成批（章节内截断由调用方做），保证每章都被审到。
    """
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for chapter in chapters or []:
        length = len(str(chapter.get("content") or ""))
        if current and (len(current) >= max_chapters or size + length > max_chars):
            batches.append(current)
            current = []
            size = 0
        current.append(chapter)
        size += length
    if current:
        batches.append(current)
    return batches


def normalize_review_findings(payload: Any, valid_node_ids: set[str]) -> list[dict[str, str]]:
    """审读结果归一：node_id 必须在本次章节集内（模型跑偏的丢弃），severity 回退 low。"""
    raw = payload.get("findings") if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        return []
    findings: list[dict[str, str]] = []
    for item in raw[:REVIEW_FINDINGS_MAX]:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or "").strip()
        if node_id not in valid_node_ids:
            continue
        issue = str(item.get("issue") or "").strip()
        if not issue:
            continue
        severity = str(item.get("severity") or "").strip().lower()
        if severity not in ("high", "medium", "low"):
            severity = "low"
        findings.append(
            {
                "node_id": node_id,
                "severity": severity,
                "where": str(item.get("where") or "").strip()[:120],
                "issue": issue[:500],
            }
        )
    return findings[:REVIEW_FINDINGS_MAX]


class BidWizardReviewAgent:
    """O5 审读-修订轮：撰写完成后的一轮 Reader→Revise（可选、一轮封顶、复用 write 计费）。

    与 BidWizardWriteAgent 同构（celery 包装/取消/SSE/结算零改动）：审读按章分批产出
    findings；仅 high/medium 触发逐章修订（落新 section 版本、SSE 推 revision_completed，
    Word 侧沿用「替换本章」交互）；low 只进报告。修订后全稿机检/覆盖矩阵按最终内容复算。
    """

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
        self._wizard_id = ""

    def _publish(self, event_type: str, data: dict[str, Any]) -> None:
        if self.event_callback is not None:
            try:
                self.event_callback(event_type, data or {})
            except Exception:
                logger.exception("bid-wizard review event publish failed: task=%s", self.task_id)

    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise BidWizardCancelled("用户取消了审读任务")

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

    async def _load_latest_chapters(
        self, wizard_id: str, spec: list[dict[str, Any]], selected: set[str]
    ) -> list[dict[str, Any]]:
        """跨任务取每个 node 的最新已生成章节（与「重新写入 Word」权威清单同口径）。"""
        spec_nodes = [node for node in spec if not selected or node.get("node_id") in selected]
        node_ids = [str(node.get("node_id") or "") for node in spec_nodes]
        if not node_ids:
            return []
        async with self.session_factory() as db:
            rows = (
                await db.execute(
                    select(BidWizardSection, BidWritingTask.created_at)
                    .join(BidWritingTask, BidWritingTask.id == BidWizardSection.task_id)
                    .where(
                        BidWritingTask.wizard_id == wizard_id,
                        BidWizardSection.node_id.in_(node_ids),
                        BidWizardSection.status.in_(("generated", "written")),
                    )
                    .order_by(BidWritingTask.created_at.desc())
                )
            ).all()
        latest_by_node: dict[str, BidWizardSection] = {}
        for section, _created in rows:  # 最新任务先到先得
            latest_by_node.setdefault(str(section.node_id), section)
        from backend.config import get_settings

        workspace = Path(get_settings().workspace_path)
        chapters: list[dict[str, Any]] = []
        for node in spec_nodes:
            section = latest_by_node.get(str(node.get("node_id")))
            if section is None:
                continue
            content = ""
            if section.content_path:
                path = Path(section.content_path)
                if not path.is_absolute():
                    path = workspace / path
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    logger.exception(
                        "bid-wizard review chapter read failed: task=%s node=%s",
                        self.task_id,
                        section.node_id,
                    )
                    continue
            if not content.strip():
                continue
            chapters.append(
                {
                    "node_id": str(section.node_id),
                    "title": str(section.title or node.get("title") or section.node_id),
                    "content": content,
                    "word_count": int(section.word_count or 0) or len(re.sub(r"\s", "", content)),
                }
            )
        return chapters

    async def _ensure_section_row(self, node: dict[str, Any]) -> None:
        async with self.session_factory() as db:
            existing = (
                await db.execute(
                    select(BidWizardSection.id).where(
                        BidWizardSection.task_id == self.task_id,
                        BidWizardSection.node_id == node["node_id"],
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
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

    async def _mark_section_failed(self, node_id: str, message: str) -> None:
        try:
            await self._set_section_status(node_id, status="failed", error_message=message[:2_000])
        except Exception:
            logger.exception(
                "bid-wizard review revision failure mark failed: task=%s node=%s", self.task_id, node_id
            )

    async def _revise_section(
        self,
        node: dict[str, Any],
        *,
        current_content: str,
        node_findings: list[dict[str, str]],
        requirements_text: str,
        spec: list[dict[str, Any]],
        score_items_text: str,
        fig_mode: str,
        global_findings_text: str = "",
        rejection_items_text: str = "",
        tender_text: str = "",
    ) -> dict[str, Any]:
        self._publish("section_started", {"node_id": node["node_id"], "title": node["title"]})
        await self._ensure_section_row(node)
        await self._set_section_status(node["node_id"], status="generating")
        findings_text = "\n".join(
            "- [%s] %s：%s" % (item["severity"], item["where"] or "本章", item["issue"])
            for item in node_findings
        )
        # 父章与子节同批时正文按全稿约定只是章首导语（与 write 路径 chapter_intro_plan 同口径）：
        # 修订同样禁止把父章扩写成全章正文——那会与独立生成的子节成对/三重重复（2026-10-24 质量轮回归）
        intro_children = chapter_intro_plan(spec).get(str(node["node_id"]))
        user_prompt = (
            REVIEW_REVISE_USER_TEMPLATE.replace("__OUTLINE_TEXT__", _outline_text(spec))
            .replace("__REQUIREMENTS_TEXT__", requirements_text or "无")
            .replace("__TENDER_REQ_TEXT__", tender_text or "（无）")
            .replace("__REJECTION_ITEMS_TEXT__", rejection_items_text or "（无）")
            .replace("__GLOBAL_FINDINGS_TEXT__", global_findings_text or "（无）")
            .replace("__SCORE_ITEMS_TEXT__", score_items_text or "（无直接对应的评分项）")
            .replace(
                "__CURRENT_CONTENT__", (current_content or "")[:REVIEW_REVISE_INPUT_MAX_CHARS]
            )
            .replace("__FINDINGS_TEXT__", findings_text or "（无）")
        )
        if intro_children:
            user_prompt += (
                "\n\n注意：本节点是父章节，其正文按全稿约定只是章首导语"
                "（其下小节由系统另行成文，不包含在本正文内）。修订后仍须保持导语形态——"
                "1-2 个自然段、只概述本章定位与各小节安排，严禁展开任何小节正文、"
                "输出小节标题、表格或图示；导语过短不是问题，与子节重复才是问题。"
            )
        body = strip_code_fence(await self.llm.generate(REVIEW_REVISE_SYSTEM_PROMPT, user_prompt))
        if not body:
            raise RuntimeError(f"章节「{node['title']}」修订结果为空")
        body = clamp_mermaid_blocks(close_unterminated_mermaid_fence(body))
        if fig_mode == "bidfig":
            body = clamp_bidfig_blocks(close_unterminated_bidfig_fence(body))
            body, fig_repair = await tighten_bidfig_fences(self.llm, body)
            if fig_repair["repaired"] or fig_repair["degraded"]:
                logger.info(
                    "bidfig tighten (review): task=%s node=%s repaired=%d degraded=%d",
                    self.task_id,
                    node["node_id"],
                    fig_repair["repaired"],
                    fig_repair["degraded"],
                )
        if intro_children:
            # 父章导语机械兜底（与 write 路径同款）：修订越界输出小节标题时截断到首个标题行之前
            body = sanitize_chapter_intro(body)
        body = strip_leading_title_headings(body, str(node.get("title") or ""))
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
        # 修订不自动写入 Word：章节此前多已写入，沿用「替换 Word 中本章」交互（防重复插入）
        self._publish(
            "revision_completed",
            {"node_id": node["node_id"], "title": node["title"], "word_count": word_count},
        )
        return {
            "node_id": node["node_id"],
            "title": node["title"],
            "word_count": word_count,
            "content": content,
            "article_count": int(node.get("article_count") or 2),
            "text_count": int(node.get("text_count") or 400),
        }

    async def auto_fix_machine_errors(
        self,
        *,
        spec: list[dict[str, Any]],
        requirements: Optional[dict[str, Any]],
        analysis: Optional[dict[str, Any]],
        sections: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
        """质检自动修复闭环：机检 error 转定向修订 finding，逐章修订后复检。

        撰写任务（生成完成后）与审读任务（修订复算后）共用。error 清零或轮次用尽即停；
        单章修订失败保留原文（原文仍是可用基线），下轮复检仍报则再试。全稿级 stance
        冲突不直接下发 detail，而是按 stance_conflict_targets 的裁决口径定向下发涉事章
        （与审读轮 ①.5 同款指令），并把裁决注入 global_findings_text 保证涉事章同一结论。
        返回 (最终章节列表, 最终机检条目, auto_fix 摘要)。
        """
        requirements = requirements or {}
        analysis = analysis or {}
        spec_by_id = {str(node.get("node_id")): node for node in spec}
        outline_ids = {str(node.get("node_id") or "") for node in spec}
        # 文档顺序＝spec 顺序（重编号/压缩挑选都依赖全稿顺序）
        order_index = {str(node.get("node_id") or ""): index for index, node in enumerate(spec)}
        current = sorted(
            (dict(section) for section in sections),
            key=lambda item: order_index.get(str(item.get("node_id") or ""), len(order_index)),
        )
        # 大纲图表规划 → 机检规划图兑现核对（⑩）
        planned_figures = {
            str(node.get("node_id") or ""): (node.get("charts") or []) for node in spec
        }

        def _all_checks(items: list[dict[str, Any]]) -> list[dict[str, str]]:
            checks = draft_checks(
                items,
                style_card=requirements.get("style_card"),
                outline_ids=outline_ids,
                planned_figures=planned_figures,
            )
            try:
                checks.extend(
                    capability_stance_issues(spec, items, requirements.get("capability_map"))
                )
            except Exception:
                logger.exception(
                    "bid-wizard auto-fix capability checks failed: task=%s", self.task_id
                )
            return checks

        def _overlong_findings(items: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
            """超稿压缩 finding（软预算收敛，2026-10-25 复盘：真实成稿为目标 180%、
            仅告警无收敛，篇幅与费用双超）：叶子节实际 > 规划×ratio 且超 600 字。"""
            ratio = _auto_fix_overlong_ratio()
            if ratio <= 0:
                return {}
            all_ids = [str(item.get("node_id") or "") for item in items]
            findings: dict[str, dict[str, str]] = {}
            for item in items:
                node_id = str(item.get("node_id") or "")
                if not node_id or node_id not in spec_by_id:
                    continue
                if any(other.startswith(node_id + ".") for other in all_ids if other != node_id):
                    continue  # 父章导语按约定精短，不参与压缩
                content = str(item.get("content") or "")
                actual = int(item.get("word_count") or 0) or len(re.sub(r"\s", "", content))
                planned = max(
                    (int(item.get("article_count") or 2)) * (int(item.get("text_count") or 400)), 1
                )
                if actual > planned * ratio and actual - planned > AUTO_FIX_OVERLONG_MIN_EXCESS:
                    findings[node_id] = {
                        "node_id": node_id,
                        "severity": "medium",
                        "where": "篇幅（超稿压缩）",
                        "issue": (
                            "本章约 %d 字，达规划篇幅（约 %d 字）的 %d%%——请把本章压缩到约 %d 字"
                            "（允许 ±20%%）：删除重复展开与空泛修饰、合并同类表格与图示，"
                            "保留全部评分项响应、关键指标/承诺与图表编号" % (
                                actual, planned, int(actual * 100 / planned), planned,
                            )
                        )[:500],
                    }
            return findings

        def _error_findings(items: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
            findings: dict[str, list[dict[str, str]]] = {}

            def _add(node_id: str, where: str, issue: str) -> None:
                if node_id and node_id in spec_by_id:
                    findings.setdefault(node_id, []).append(
                        {"node_id": node_id, "severity": "high", "where": where, "issue": issue[:500]}
                    )

            for issue in _all_checks(items):
                if str(issue.get("severity")) != "error":
                    continue
                node_id = str(issue.get("node_id") or "")
                issue_type = str(issue.get("type") or "")
                detail = str(issue.get("detail") or "")
                if not node_id:
                    if issue_type == "forbidden_word":
                        # 全稿级禁用词分派到实际含残留词的章节（其余全稿级 error 由
                        # stance targets 按裁决口径下发）
                        from backend.agent.bid_doc_checks import _forbidden_residual_count

                        for item in items:
                            candidate = str(item.get("node_id") or "")
                            content = str(item.get("content") or "")
                            for raw in (requirements.get("style_card") or {}).get("forbidden_words") or []:
                                word = str(raw or "").strip()
                                if word and _forbidden_residual_count(content, word)[0]:
                                    hint = AUTO_FIX_TYPE_HINTS.get("forbidden_word", "")
                                    _add(
                                        candidate,
                                        "机检（forbidden_word）",
                                        ("口径卡禁用词「%s」在本章出现（技术术语除外）——%s" % (word, hint))[:500],
                                    )
                        continue
                    continue  # 全稿级（stance_conflict）由下方 targets 按裁决口径下发
                hint = AUTO_FIX_TYPE_HINTS.get(issue_type)
                _add(
                    node_id,
                    "机检（%s）" % issue_type or "机检",
                    (detail + ("——" + hint if hint else ""))[:500],
                )
            try:
                for target in stance_conflict_targets(items):
                    ruling = str(target.get("ruling") or "")
                    if not ruling:
                        continue
                    for affected in sorted(
                        (target.get("open_nodes") or set()) | (target.get("restricted_nodes") or set())
                    ):
                        _add(
                            affected,
                            "全稿口径（%s）" % target["topic"],
                            (
                                "（机检自动检出）全稿对「%s」并存多种口径，统一裁决为：%s。"
                                "请把本章与该裁决冲突的表述改写为裁决口径"
                                "（自动降级/无条件启用在线等表述删除或改为「经授权后可启用」），"
                                "本章内前后矛盾的同类表述一并对齐。" % (target["topic"], ruling)
                            )[:500],
                        )
            except Exception:
                logger.exception(
                    "bid-wizard auto-fix stance findings failed: task=%s", self.task_id
                )
            return findings

        checks = _all_checks(current)
        errors = [item for item in checks if str(item.get("severity")) == "error"]
        overlong = _overlong_findings(current)
        info: dict[str, Any] = {
            "enabled": _auto_fix_enabled(),
            "rounds": 0,
            "initial_errors": len(errors),
            "fixed_total": 0,
            "remaining_errors": len(errors),
            "revised_nodes": [],
            "renumbered_captions": 0,
            "renumbered_nodes": [],
            "overlong_initial": len(overlong),
            "compressed_nodes": [],
        }
        if not info["enabled"]:
            return current, checks, info

        # 确定性图表编号重排（零 LLM，2026-10-25 复盘：图5-1×2/表7-1×3 各节独立起号）：
        # 每章每类按题注出现顺序编为 X-1..N，题注与引用同步替换；改动章节回写落盘
        renumbered, renum_stats = renumber_captions(current)
        if renum_stats.get("changed_nodes"):
            for item in renumbered:
                node_id = str(item.get("node_id") or "")
                if node_id not in renum_stats["changed_nodes"]:
                    continue
                content = str(item.get("content") or "")
                try:
                    section_path = self.workspace_dir / "sections" / f"{_safe_filename(node_id)}.md"
                    section_path.parent.mkdir(parents=True, exist_ok=True)
                    section_path.write_text(content, encoding="utf-8")
                    await self._set_section_status(
                        node_id,
                        status="generated",
                        content_path=str(section_path),
                        word_count=len(re.sub(r"\s", "", content)),
                        error_message=None,
                    )
                except Exception:
                    logger.exception(
                        "bid-wizard auto-fix caption renumber persist failed: task=%s node=%s",
                        self.task_id, node_id,
                    )
            current = renumbered
            info["renumbered_captions"] = int(renum_stats.get("remapped") or 0)
            info["renumbered_nodes"] = list(renum_stats["changed_nodes"])
            logger.info(
                "bid-wizard caption renumber: task=%s captions=%d remapped=%d nodes=%d ambiguous=%d",
                self.task_id, renum_stats.get("captions"), renum_stats.get("remapped"),
                len(renum_stats["changed_nodes"]), renum_stats.get("ambiguous"),
            )
            checks = _all_checks(current)
            errors = [item for item in checks if str(item.get("severity")) == "error"]
            overlong = _overlong_findings(current)
            info["overlong_initial"] = len(overlong)

        if not errors and not overlong:
            return current, checks, info

        requirements_text = build_requirements_text(requirements)
        scoring_items = scoring_items_of(analysis)
        generation_options = generation_options_of(requirements)
        fig_mode = (
            str(generation_options.get("fig_mode") or "bidfig") if generation_options else "bidfig"
        )
        rejections_text = rejection_items_text(analysis)
        tender_text = tender_requirements_text(analysis)

        for round_no in range(1, AUTO_FIX_MAX_ROUNDS + 1):
            self._check_cancel()
            error_findings = _error_findings(current)
            overlong_findings = _overlong_findings(current)
            findings: dict[str, list[dict[str, str]]] = {}
            for node_id in set(error_findings) | set(overlong_findings):
                merged = list(error_findings.get(node_id, []))
                if node_id in overlong_findings:
                    merged.append(overlong_findings[node_id])
                findings[node_id] = merged
            affected = [
                str(node.get("node_id"))
                for node in spec
                if str(node.get("node_id")) in findings
            ][:AUTO_FIX_MAX_NODES_PER_ROUND]
            if not affected:
                break
            info["rounds"] = round_no
            self._publish(
                "phase",
                {"phase": "auto_fixing", "round": round_no, "section_total": len(affected)},
            )
            # 全稿口径基准：立场裁决下发给每个涉事章的修订调用，各章按同一结论对齐
            global_findings_text = (
                "\n".join(
                    "- %s" % finding["issue"]
                    for node_id in affected
                    for finding in error_findings.get(node_id, [])
                    if str(finding.get("where") or "").startswith("全稿口径")
                )[:4_200]
                or "（无）"
            )
            for node_id in affected:
                self._check_cancel()
                section = next(
                    (item for item in current if str(item.get("node_id")) == node_id), None
                )
                if section is None:
                    continue
                if node_id in overlong_findings and node_id not in info["compressed_nodes"]:
                    info["compressed_nodes"].append(node_id)
                try:
                    revised = await self._revise_section(
                        spec_by_id[node_id],
                        current_content=str(section.get("content") or ""),
                        node_findings=findings[node_id],
                        requirements_text=requirements_text,
                        spec=spec,
                        score_items_text=render_score_items_text(
                            score_items_for_node(spec, spec_by_id[node_id], scoring_items)
                        ),
                        fig_mode=fig_mode,
                        global_findings_text=global_findings_text,
                        rejection_items_text=rejections_text,
                        tender_text=tender_text,
                    )
                except BidWizardCancelled:
                    raise
                except Exception:
                    logger.exception(
                        "bid-wizard auto-fix revise failed: task=%s node=%s", self.task_id, node_id
                    )
                    continue
                current = [
                    revised if str(item.get("node_id")) == node_id else item for item in current
                ]
                if node_id not in info["revised_nodes"]:
                    info["revised_nodes"].append(node_id)
            checks = _all_checks(current)
            errors = [item for item in checks if str(item.get("severity")) == "error"]
            if not errors and not _overlong_findings(current):
                break
        info["remaining_errors"] = len(errors)
        info["fixed_total"] = max(0, int(info["initial_errors"]) - len(errors))
        return current, checks, info

    async def run(self) -> dict[str, Any]:
        context = await self._load_context()
        task = context["task"]
        wizard = context["wizard"]
        self._wizard_id = wizard.id
        spec = context["spec"]
        requirements = context["requirements"] or {}
        chapters = await self._load_latest_chapters(wizard.id, spec, context["selected"])
        if not chapters:
            raise RuntimeError("没有可审读的已生成章节")

        requirements_text = build_requirements_text(requirements)
        generation_options = generation_options_of(requirements)
        fig_mode = str(generation_options.get("fig_mode") or "bidfig") if generation_options else "bidfig"
        scoring_items = scoring_items_of(context["analysis"])
        rejections_text = rejection_items_text(context["analysis"])
        tender_text = tender_requirements_text(context["analysis"])
        spec_by_id = {str(node.get("node_id")): node for node in spec}

        # ① 审读（Reader）：按章分批；单批失败不终止整轮（审读是增值非必需环节）
        self._publish("phase", {"phase": "reviewing", "section_total": len(chapters)})
        findings: list[dict[str, str]] = []
        valid_nodes = {chapter["node_id"] for chapter in chapters}
        for batch in batch_review_chapters(chapters):
            self._check_cancel()
            chapters_text = "\n\n".join(
                "### 章节 %s %s\n%s" % (chapter["node_id"], chapter["title"], chapter["content"][:REVIEW_CHAPTER_MAX_CHARS])
                for chapter in batch
            )
            try:
                payload = await self.llm.generate_json(
                    REVIEW_FINDINGS_SYSTEM_PROMPT,
                    REVIEW_FINDINGS_USER_TEMPLATE.replace("__OUTLINE_TEXT__", _outline_text(spec))
                    .replace("__REQUIREMENTS_TEXT__", (requirements_text or "无")[:SPEC_CONTEXT_MAX_CHARS])
                    .replace("__TENDER_REQ_TEXT__", tender_text or "（无）")
                    .replace("__REJECTION_ITEMS_TEXT__", rejections_text or "（无）")
                    .replace("__CHAPTERS__", chapters_text),
                )
                findings.extend(normalize_review_findings(payload, valid_nodes))
                self._publish("review_findings", {"total": len(findings)})
            except BidWizardCancelled:
                raise
            except Exception:
                logger.exception("bid-wizard review findings batch failed: task=%s", self.task_id)
        findings = findings[:REVIEW_FINDINGS_MAX]

        # ①.5 口径冲突定向收敛（2026-10-24 第 2 轮质量轮）：机检检出跨章口径冲突后，
        # 涉事章若无 Reader finding 就不会进修订集（上轮统一方案只落实到有意见的 2.4/2.5
        # 的机制根因）——给每个涉事章补一条带裁决口径的 high finding，驱动各章按同一口径改写
        try:
            for target in stance_conflict_targets(chapters):
                ruling = str(target.get("ruling") or "")
                if not ruling:
                    continue
                affected_nodes = sorted(
                    (target.get("open_nodes") or set()) | (target.get("restricted_nodes") or set())
                )
                for affected in affected_nodes:
                    if affected not in valid_nodes:
                        continue
                    findings.append(
                        {
                            "node_id": affected,
                            "severity": "high",
                            "where": "全稿口径（%s）" % target["topic"],
                            "issue": (
                                "（机检自动检出）全稿对「%s」并存多种口径，统一裁决为：%s。"
                                "请把本章与该裁决冲突的表述改写为裁决口径"
                                "（自动降级/无条件启用在线等表述删除或改为「经授权后可启用」），"
                                "本章内前后矛盾的同类表述一并对齐。" % (target["topic"], ruling)
                            )[:500],
                        }
                    )
            self._publish("review_findings", {"total": len(findings)})
        except Exception:
            logger.exception("bid-wizard stance convergence findings failed: task=%s", self.task_id)

        # ①.6 图表占位定向清理（2026-10-24 第 3 轮质量轮）：chart_placeholder 机检上轮只报警，
        # 修订触发仍靠 Reader 配额（覆盖随机漏检、暗标主题 3 个 error 卡 gate）——转为 per-node
        # medium finding 驱动修订，同主题他章建议值可复用
        try:
            for issue in chart_placeholder_issues(chapters):
                if issue.get("node_id") in valid_nodes:
                    findings.append(
                        {
                            "node_id": str(issue["node_id"]),
                            "severity": "medium",
                            "where": "图表",
                            "issue": "（机检自动检出）%s" % str(issue.get("detail") or "")[:450],
                        }
                    )
            self._publish("review_findings", {"total": len(findings)})
        except Exception:
            logger.exception("bid-wizard chart placeholder findings failed: task=%s", self.task_id)

        # ①.7 同源跨章意见传播（第 3 轮质量轮）：Reader 的归并意见常在 issue 里点名关联章节
        # （「并见 1.1 末段、1.3 表第 8 行…三处同步统一」），但修订只按 node_id 触发主章——
        # 把 issue 文本点名的其他章节号复制为 per-node finding，同步意见才能真正落到各章
        try:
            propagated: list[dict[str, str]] = []
            for item in findings:
                if item["severity"] not in ("high", "medium"):
                    continue
                issue_text = item["issue"]
                mentioned = [
                    other
                    for other in sorted(valid_nodes, key=len, reverse=True)
                    if other != item["node_id"]
                    and re.search(r"(?<![\d.])%s(?![\d.])" % re.escape(other), issue_text)
                ]
                for other in mentioned[:3]:
                    propagated.append(
                        {
                            "node_id": other,
                            "severity": item["severity"],
                            "where": item["where"] or "本章",
                            "issue": "（同源跨章意见，主章 %s）%s"
                            % (item["node_id"], issue_text)[:500],
                        }
                    )
            findings.extend(propagated)
        except Exception:
            logger.exception("bid-wizard finding propagation failed: task=%s", self.task_id)

        # ② 修订（Revise）：仅 high/medium 触发、按大纲序、一轮封顶；low 只进报告
        actionable = [
            chapter["node_id"]
            for chapter in chapters
            if any(
                item["node_id"] == chapter["node_id"] and item["severity"] in ("high", "medium")
                for item in findings
            )
        ][:REVIEW_MAX_REVISIONS]
        self._publish("phase", {"phase": "revising", "section_total": len(actionable)})
        # 全稿口径基准：跨章 high 发现注入每个修订调用——口径/指标/承诺类问题跨章可见，
        # 修订代理才能按同一结论对齐（2026-10-24 质量轮：验收指标四章分裂、在线算力三套说法）；
        # 固定首行=口径呈现基准（第 3 轮质量轮：1.4 口径声明与 1.2 口径定义分裂为裸（请补充））
        global_findings_text = (
            "全稿口径呈现基准：confirm 态条目一律写「本项目建设内容（具体规则需求确认阶段固化）」"
            "并保留交付时限锚点；口径声明/对照表内不以裸「（请补充）」作为口径值，"
            "素材无实际编号时用功能名标注。\n"
            + "\n".join(
                "- [%s] %s %s：%s"
                % (item["severity"], item["node_id"], item["where"] or "", item["issue"])
                for item in findings
                if item["severity"] == "high"
            )
        )[:4_200] or "（无）"
        revised_map: dict[str, dict[str, Any]] = {}
        for node_id in actionable:
            self._check_cancel()
            node = spec_by_id.get(node_id)
            if node is None:
                continue
            chapter = next(item for item in chapters if item["node_id"] == node_id)
            node_findings = [
                item
                for item in findings
                if item["node_id"] == node_id and item["severity"] in ("high", "medium")
            ]
            try:
                revised_map[node_id] = await self._revise_section(
                    node,
                    current_content=chapter["content"],
                    node_findings=node_findings,
                    requirements_text=requirements_text,
                    spec=spec,
                    score_items_text=render_score_items_text(
                        score_items_for_node(spec, node, scoring_items)
                    ),
                    fig_mode=fig_mode,
                    global_findings_text=global_findings_text,
                    rejection_items_text=rejections_text,
                    tender_text=tender_text,
                )
            except BidWizardCancelled:
                raise
            except Exception as exc:
                message = str(exc)[:500]
                await self._mark_section_failed(node_id, message)
                self._publish("section_failed", {"node_id": node_id, "error": message})

        self._check_cancel()
        # 修订后全稿状态复算（final = 修订版优先，未修订章用原内容）
        final_sections: list[dict[str, Any]] = []
        for chapter in chapters:
            result = revised_map.get(chapter["node_id"])
            if result is not None:
                final_sections.append(result)
                continue
            node = spec_by_id.get(chapter["node_id"], {})
            final_sections.append(
                {
                    "node_id": chapter["node_id"],
                    "title": chapter["title"],
                    "content": chapter["content"],
                    "word_count": chapter["word_count"],
                    "article_count": int(node.get("article_count") or 2),
                    "text_count": int(node.get("text_count") or 400),
                }
            )
        review_of = ""
        if isinstance(task.summary, dict):
            review_of = str(task.summary.get("review_of") or "")
        # 质检自动修复闭环（2026-10-25，同撰写路径）：修订复算后机检仍有 error 时继续
        # 定向修复+复检直至清零——修订掷骰子引入的新缺陷也在这里兜住
        checks: list[dict[str, str]] = []
        auto_fix_info: dict[str, Any] = {}
        try:
            final_sections, checks, auto_fix_info = await self.auto_fix_machine_errors(
                spec=spec,
                requirements=requirements,
                analysis=context["analysis"],
                sections=final_sections,
            )
        except BidWizardCancelled:
            raise
        except Exception:
            logger.exception("bid-wizard review draft_checks/auto-fix failed: task=%s", self.task_id)
        summary: dict[str, Any] = {
            "action": "review",
            "review_of": review_of,
            "chapters_total": len(chapters),
            "findings": findings,
            "findings_total": len(findings),
            "revised_nodes": list(revised_map),
            "revised_total": len(revised_map),
            "word_count": sum(int(item.get("word_count") or 0) for item in final_sections),
            "auto_fix": auto_fix_info,
        }
        summary["doc_checks"] = checks
        try:
            summary["score_coverage"] = score_coverage_matrix(
                spec, [chapter["node_id"] for chapter in chapters], scoring_items
            )
        except Exception:
            logger.exception("bid-wizard review score_coverage failed: task=%s", self.task_id)
        try:
            summary["fig_stats"] = fig_stats_of(final_sections, fig_mode)
        except Exception:
            logger.exception("bid-wizard review fig_stats failed: task=%s", self.task_id)
        return summary
