"""招标文件解析要素提取 agent（doc/招标文件解析需求.md §4.4/§4.5）。

与编标向导的 analyze_tender（bullet 列表口径）不同：本模块面向「解析结果详情页」
六 Tab 三列表格（字段名 | 内容 | 原文位置），每个字段带 source 原文位置
（最低口径：章节/条款标题 + 关键句摘录，需求 §8 Q9 默认 P0 纯文本）。
LLM 客户端复用 WizardLLM（instrumented，用量自动入 usage 上下文）。
"""

from __future__ import annotations

import logging
from typing import Any

from backend.agent.bid_wizard_agent import WizardLLM

logger = logging.getLogger(__name__)

# 与向导 analysis 同口径的上下文上限（约 12 万字符；超出截断提示）
TENDER_PARSE_CONTEXT_MAX_CHARS = 120_000

# 固定字段 Tab：LLM 必须按固定字段名输出（缺项归一化补空行，页面作核对清单）
TAB_BASIC_FIELDS: tuple[str, ...] = (
    "项目名称",
    "项目编号",
    "招标方名称",
    "开标时间",
    "投标截止时间",
    "预算金额",
    "保证金",
    "工期/服务期",
)
TAB_QUALIFICATION_FIELDS: tuple[str, ...] = (
    "资质等级",
    "财务要求",
    "业绩要求",
    "人员要求",
    "信用要求",
)
TAB_COMMERCIAL_FIELDS: tuple[str, ...] = (
    "付款方式",
    "交付/到货期",
    "质保期",
    "违约责任",
    "售后要求",
)
FIXED_TABS: dict[str, tuple[str, ...]] = {
    "basic": TAB_BASIC_FIELDS,
    "qualification": TAB_QUALIFICATION_FIELDS,
    "commercial": TAB_COMMERCIAL_FIELDS,
}
# 动态行 Tab：行数不定（评分项逐条 / 技术需求逐条 / 其他注意事项）
DYNAMIC_TABS: dict[str, str] = {
    "scoring": "评分办法",
    "technical": "技术需求",
    "others": "其他内容",
}
TAB_ORDER: tuple[str, ...] = (
    "basic",
    "qualification",
    "scoring",
    "technical",
    "commercial",
    "others",
)

# 行字段截断上限（DB JSON 列与页面可读性平衡）
_FIELD_MAX = 200
_VALUE_MAX = 4_000
_SOURCE_MAX = 1_000
_DYNAMIC_ROWS_MAX = 200

SYSTEM_PROMPT = (
    "你是招标文件解析专家，从招标文件文本中提取结构化要素，并标注每项内容的原文位置。"
    "只输出一个合法 JSON 对象，不要输出任何其他文字或代码块标记。"
)

USER_TEMPLATE = """以下是招标文件文本（可能截断）：

__TENDER_TEXT__

请提取为如下 JSON。六个分组对应详情页六个页签，每行是 {字段名, 内容, 原文位置} 三元组：
- basic/qualification/commercial 的字段名固定（用下方给出的字段名，顺序不限，提取不到的填空字符串）；
- scoring/technical/others 是动态行（有几条写几条，没有就空数组）。

{
  "basic": [
    {"field": "项目名称", "value": "", "source": ""},
    {"field": "项目编号", "value": "", "source": ""},
    {"field": "招标方名称", "value": "", "source": ""},
    {"field": "开标时间", "value": "", "source": ""},
    {"field": "投标截止时间", "value": "", "source": ""},
    {"field": "预算金额", "value": "", "source": ""},
    {"field": "保证金", "value": "", "source": ""},
    {"field": "工期/服务期", "value": "", "source": ""}
  ],
  "qualification": [
    {"field": "资质等级", "value": "", "source": ""},
    {"field": "财务要求", "value": "", "source": ""},
    {"field": "业绩要求", "value": "", "source": ""},
    {"field": "人员要求", "value": "", "source": ""},
    {"field": "信用要求", "value": "", "source": ""}
  ],
  "scoring": [
    {"field": "评标方法", "value": "如：综合评分法", "source": ""},
    {"field": "评分项名称", "value": "该评分项的分值与评分标准", "source": ""}
  ],
  "technical": [
    {"field": "需求/参数名称", "value": "具体参数与要求；招标文件标注★或▲的重要条款在内容开头保留该标记", "source": ""}
  ],
  "commercial": [
    {"field": "付款方式", "value": "", "source": ""},
    {"field": "交付/到货期", "value": "", "source": ""},
    {"field": "质保期", "value": "", "source": ""},
    {"field": "违约责任", "value": "", "source": ""},
    {"field": "售后要求", "value": "", "source": ""}
  ],
  "others": [
    {"field": "废标/否决条款", "value": "逐条列出", "source": ""},
    {"field": "答疑与补遗截止时间", "value": "", "source": ""}
  ]
}

规则：
1. 「内容」忠实转述或摘录原文关键信息，保留数字、日期、金额、单位；提取不到用空字符串，严禁虚构；
2. 「原文位置」= 所在章节/条款标题 + 关键句摘录（合计 80 字以内），如「第三章 投标须知前附表第8项：投标保证金金额为贰万元整」；解析文本无页码信息，不要编造页码；
3. 「内容」为空的行「原文位置」也留空；
4. technical 逐条覆盖招标文件的技术需求/参数表（可多行）；others 收录废标/否决条款、答疑补遗时间与其他注意事项；条数多时保持每条一句话的粒度。"""


def _clean_row(raw: Any) -> dict[str, str] | None:
    """Single row → {field, value, source} with truncation; None to drop."""
    if not isinstance(raw, dict):
        return None
    field = str(raw.get("field") or raw.get("字段名") or "").strip()
    value = str(raw.get("value") or raw.get("内容") or "").strip()
    source = str(raw.get("source") or raw.get("原文位置") or "").strip()
    if not field and not value:
        return None
    return {
        "field": field[:_FIELD_MAX],
        "value": value[:_VALUE_MAX],
        "source": source[:_SOURCE_MAX] if value else "",
    }


def _normalize_fixed_tab(rows: Any, fields: tuple[str, ...]) -> list[dict[str, str]]:
    """固定字段 Tab：按标准字段名对齐补全；LLM 多出的有值行追加在尾部。"""
    by_field: dict[str, dict[str, str]] = {}
    extra: list[dict[str, str]] = []
    for raw in rows if isinstance(rows, list) else []:
        row = _clean_row(raw)
        if row is None:
            continue
        key = row["field"]
        if key in fields:
            # 后行覆盖前行（模型重复输出时保留信息更全的一条）
            prev = by_field.get(key)
            if prev is None or len(row["value"]) >= len(prev["value"]):
                by_field[key] = row
        elif row["value"]:
            extra.append(row)
    normalized = [
        by_field.get(field, {"field": field, "value": "", "source": ""}) for field in fields
    ]
    return normalized + extra[:_DYNAMIC_ROWS_MAX]


def _normalize_dynamic_tab(rows: Any, default_field: str) -> list[dict[str, str]]:
    """动态行 Tab：保留有内容的行；字段名缺失时用 Tab 兜底标签。"""
    normalized: list[dict[str, str]] = []
    for raw in rows if isinstance(rows, list) else []:
        row = _clean_row(raw)
        if row is None or not row["value"]:
            continue
        if not row["field"]:
            row["field"] = default_field
        normalized.append(row)
        if len(normalized) >= _DYNAMIC_ROWS_MAX:
            break
    return normalized


def normalize_parse_result(raw: Any) -> dict[str, list[dict[str, str]]]:
    """LLM 原始输出 → 六 Tab 标准结构（键齐全、固定字段补全、动态行过滤）。"""
    result: dict[str, list[dict[str, str]]] = {}
    source = raw if isinstance(raw, dict) else {}
    for tab in TAB_ORDER:
        if tab in FIXED_TABS:
            result[tab] = _normalize_fixed_tab(source.get(tab), FIXED_TABS[tab])
        else:
            result[tab] = _normalize_dynamic_tab(source.get(tab), DYNAMIC_TABS[tab])
    return result


def find_field_value(result: dict[str, list[dict[str, str]]], tab: str, field: str) -> str:
    for row in result.get(tab, []):
        if row.get("field") == field:
            return row.get("value") or ""
    return ""


async def extract_tender_elements(llm: WizardLLM, tender_markdown: str) -> dict[str, list[dict[str, str]]]:
    """两段解析第二段：markdown → 六 Tab 行模型（含原文位置）。"""
    tender_text = (tender_markdown or "")[:TENDER_PARSE_CONTEXT_MAX_CHARS]
    if len(tender_markdown or "") > TENDER_PARSE_CONTEXT_MAX_CHARS:
        tender_text += "\n…（后文已截断）"
    user_prompt = USER_TEMPLATE.replace("__TENDER_TEXT__", tender_text)
    raw = await llm.generate_json(SYSTEM_PROMPT, user_prompt)
    if not isinstance(raw, dict):
        raise RuntimeError("招标要素提取结果不是 JSON 对象")
    result = normalize_parse_result(raw)
    # 全空兜底：六 Tab 一行有值的内容都没有时视为提取失败（正常招标文件至少有项目名称）
    has_any = any(row.get("value") for rows in result.values() for row in rows)
    if not has_any:
        raise RuntimeError("未能从该文件中提取到任何招标要素，请确认文件内容")
    return result
