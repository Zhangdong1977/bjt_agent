"""总体报告生成 Agent — 检查完成后汇总各子 agent 输出，生成结构化总体报告.

设计要点：
- 输入：TodoItem（各大类 check_items / result）+ ReviewResult（非合规 findings，
  评分类大类的合规项说明从 TodoItem.result 补充取）。
- 统计、分组、废标风险评级全部由代码确定性计算，不依赖 LLM 算数：
  高 = 存在涉及废标条款的严重风险；中 = 存在严重/重要风险但无废标相关；
  低 = 仅一般风险或无风险。
- 一次 LLM 调用只负责三件事：
  1) 每个大类×等级的风险描述精简合并（一句话、同大类多项合并、不加建议）；
  2) 规则标记（A009/B001/C001）之外的严重风险项是否"涉及废标条款"的语义判定；
  3) 评分类大类（D001/D002/D003）的满分/预估得分提取。
- LLM 失败 / 输出不合法 → 降级为规则聚合版（原文摘录截断，degraded=true），
  不向上抛异常，绝不阻塞审查主流程。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select

from backend.services.llm_factory import create_llm_client

from mini_agent.schema import Message

logger = logging.getLogger(__name__)

# 规则直接判定"涉及废标条款"的大类编码：A009 否决条款 / B001 资格项 / C001 加星重点项
REJECTION_RULE_DOC_CODES = ("A009", "B001", "C001")
# 评分类大类编码（满分/预估得分提取来源）
SCORE_RULE_DOC_CODES = ("D001", "D002", "D003")

SEVERITY_ORDER = ("critical", "major", "minor")
SEVERITY_LABELS = {"critical": "严重", "major": "重要", "minor": "一般"}
_SEVERITY_RANK = {"critical": 3, "major": 2, "minor": 1}

SCHEMA_VERSION = 1

_REQUIREMENT_TRUNC = 200
_ISSUE_TRUNC = 200
_CHECK_ITEM_TRUNC = 80
_SCORE_NOTE_TRUNC = 300
_FALLBACK_PART_TRUNC = 50
_FALLBACK_SUMMARY_MAX = 160
_MAX_SCORE_EVIDENCE = 12

REPORT_SYSTEM_PROMPT = (
    "你是标书审查总体报告撰写专家。你的任务是汇总各检查大类的审查发现，"
    "提炼成供投标人阅读的总体风险报告素材。"
    "语言要求：专业、客观、精炼，只描述问题本身，不添加改进建议，不夸大、不臆造。"
    "只输出一个合法 JSON 对象，不要输出任何其他文字或代码块标记。"
)

REPORT_USER_PROMPT = """以下是某投标文件审查的结构化结果（按检查大类组织，severity: critical=严重/major=重要/minor=一般）：

__INPUT_JSON__

请基于以上内容，严格输出如下 JSON（不要虚构未出现的大类或风险，不要改变风险数量）：

{
  "category_summaries": {
    "<大类编码>": {"critical": "一句话", "major": "一句话", "minor": "一句话"}
  },
  "rejection_judgments": {"<风险项id>": true},
  "rejection_reason": "一句话",
  "score_items": [
    {"code": "D001", "name": "业绩得分检查", "full_score": 8, "estimated_score": 5, "note": "一句话"}
  ]
}

规则：
1. category_summaries：仅对存在风险项（risk_groups 非空）的大类×等级输出；每条描述用一句话（不超过 60 字）概括该组风险的核心问题，同一大类多个风险项的要点用分号合并；只描述问题，不写整改建议；某大类没有某等级的风险时省略该键。
2. rejection_judgments：仅对 severity=critical 且所属大类 rejection_by_rule=false 的风险项（id 取 risk_groups[].id）逐项判断——该风险对应的要求若不满足，是否会导致投标被否决/废标/无效（依据 requirement 中是否含"否决/废标/无效/不予受理"等后果表述，或属于未按要求签字盖章、报价超最高限价、资格证明材料缺失等典型废标情形）。涉及为 true，不涉及为 false。
3. rejection_reason：针对全部严重风险与废标判定的总体结论，一句话说明是否触及废标条款及主要触发点；没有严重风险时说明整体风险水平。
4. score_items：仅针对 is_scoring=true 的大类，从 score_evidence 的 note 文本中提取"满分分值/可得（预估）分值/损失分值"；full_score、estimated_score 为数字（确实提取不到填 null）；note 一句话说明得分/失分要点；没有评分类大类时输出空数组。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute-or-item accessor: works for ORM models and plain dicts."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _truncate(text: Any, limit: int) -> Optional[str]:
    s = str(text).strip() if text is not None else ""
    if not s:
        return None
    return s if len(s) <= limit else s[:limit] + "…"


def _rule_doc_code(name: str) -> str:
    """规则文档编号：文件名第一个空格前的部分，如 'A009 明确否决条款检查.md' → 'A009'."""
    return (name or "").split(" ", 1)[0].replace(".md", "")


def _rule_doc_label(name: str) -> str:
    return (name or "").replace(".md", "").strip() or "未分类"


def _normalize_severity(severity: Any) -> str:
    s = str(severity or "").lower()
    return s if s in SEVERITY_LABELS else "major"


def _parse_json(content: str) -> Optional[dict]:
    """解析 LLM 输出的 JSON 对象；容忍代码围栏与前后杂文，失败返回 None."""
    text = (content or "").strip()
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _num_or_none(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().rstrip("%").rstrip("分")
        try:
            return float(s)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# 输入组装（纯函数，便于单测）
# ---------------------------------------------------------------------------

def _score_evidence(todo: Any) -> list[dict]:
    """评分类大类的得分信息来源：该大类子 agent 的全部 findings（合规项的
    说明里也带"满分/可得分/损失分值"），取 explanation/bid_content 摘要。"""
    result = _get(todo, "result") or {}
    findings = result.get("findings") or []
    out: list[dict] = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        note = f.get("explanation") or f.get("bid_content") or ""
        if not str(note).strip():
            continue
        out.append({
            "check_item": _truncate(f.get("check_item_name"), _CHECK_ITEM_TRUNC) or "",
            "note": _truncate(note, _SCORE_NOTE_TRUNC),
            "is_compliant": bool(f.get("is_compliant")),
        })
    return out[:_MAX_SCORE_EVIDENCE]


def build_report_input(todos: list[Any], findings: list[Any]) -> dict:
    """从 TodoItem / ReviewResult 组装 LLM 输入与确定性统计.

    风险项按"检查项"去重（同一检查项多条 finding 合为一组，取最重等级），
    与前端结果页 risk_item_count 的口径一致。
    """
    # 1) 非合规 findings → 按大类+检查项分组
    groups_by_category: dict[str, list[dict]] = {}
    identity_map: dict[tuple[str, str], dict] = {}
    for f in findings:
        if bool(_get(f, "is_compliant")):
            continue  # review_results 只存非合规，防御性过滤
        cat_name = _get(f, "rule_doc_name") or "未分类"
        ident = _get(f, "check_item_name") or _get(f, "requirement_key") or ""
        if not ident:
            ident = f"req-{sum(len(v) for v in groups_by_category.values()) + 1}"
        key = (cat_name, str(ident))
        severity = _normalize_severity(_get(f, "severity"))
        g = identity_map.get(key)
        if g is None:
            g = {
                "id": None,  # 稍后统一编号
                "severity": severity,
                "check_item": _truncate(_get(f, "check_item_name"), _CHECK_ITEM_TRUNC),
                "requirement": _truncate(_get(f, "requirement_content"), _REQUIREMENT_TRUNC),
                "issue": _truncate(_get(f, "explanation"), _ISSUE_TRUNC),
                "count": 0,
            }
            identity_map[key] = g
            groups_by_category.setdefault(cat_name, []).append(g)
        g["count"] += 1
        if _SEVERITY_RANK[severity] > _SEVERITY_RANK[g["severity"]]:
            g["severity"] = severity

    # 2) 大类清单：以 todo 为主（保持 created_at 顺序），findings 兜底没跑 todo 的大类
    categories: list[dict] = []
    seen_names: set[str] = set()
    check_item_count = 0
    failed_categories: list[str] = []

    def _make_category(name: str, check_items: Any, status: str, result: Any) -> dict:
        code = _rule_doc_code(name)
        is_scoring = code in SCORE_RULE_DOC_CODES
        return {
            "code": code,
            "name": _rule_doc_label(name),
            "check_item_count": len(check_items or []),
            "status": status,
            "rejection_by_rule": code in REJECTION_RULE_DOC_CODES,
            "is_scoring": is_scoring,
            "risk_groups": groups_by_category.get(name, []),
            "score_evidence": _score_evidence({"result": result}) if is_scoring else [],
        }

    for todo in todos:
        name = _get(todo, "rule_doc_name") or "未分类"
        if name in seen_names:
            continue
        seen_names.add(name)
        check_items = _get(todo, "check_items")
        check_item_count += len(check_items or [])
        status = str(_get(todo, "status") or "")
        if status == "failed":
            failed_categories.append(_rule_doc_label(name))
        categories.append(
            _make_category(name, check_items, status, _get(todo, "result"))
        )

    for name in sorted(set(groups_by_category) - seen_names):
        categories.append(_make_category(name, [], "unknown", None))

    # 统一风险项编号（LLM 判定废标时的引用键），大类按编码字典序稳定排序
    categories.sort(key=lambda c: c["code"])
    dist = {sev: 0 for sev in SEVERITY_ORDER}
    for cat in categories:
        for i, g in enumerate(cat["risk_groups"], start=1):
            g["id"] = f"{cat['code']}#{i}"
            dist[g["severity"]] += 1

    risk_item_count = sum(dist.values())
    summary = {
        "category_count": len(todos),
        "check_item_count": check_item_count,
        "risk_item_count": risk_item_count,
        "severity_dist": dist,
        "failed_categories": failed_categories,
    }
    return {"summary": summary, "categories": categories}


# ---------------------------------------------------------------------------
# LLM 精简（一次调用 + 校验）
# ---------------------------------------------------------------------------

def build_user_prompt(report_input: dict) -> str:
    input_json = json.dumps(report_input, ensure_ascii=False)
    return REPORT_USER_PROMPT.replace("__INPUT_JSON__", input_json)


def _validated_summaries(llm_output: dict) -> dict[str, dict[str, str]]:
    raw = llm_output.get("category_summaries")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for code, levels in raw.items():
        if not isinstance(levels, dict):
            continue
        clean = {}
        for sev in SEVERITY_ORDER:
            v = levels.get(sev)
            if isinstance(v, str) and v.strip():
                clean[sev] = v.strip()[:_FALLBACK_SUMMARY_MAX + 60]
        if clean:
            out[str(code)] = clean
    return out


def _validated_judgments(llm_output: dict) -> dict[str, bool]:
    raw = llm_output.get("rejection_judgments")
    if not isinstance(raw, dict):
        return {}
    return {str(k): bool(v) for k, v in raw.items() if v is True}


def _validated_score_items(llm_output: dict, categories: list[dict]) -> list[dict]:
    raw = llm_output.get("score_items")
    if not isinstance(raw, list):
        return []
    name_by_code = {c["code"]: c["name"] for c in categories}
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        # 只保留评分类大类，防止 LLM 幻觉出其它条目
        if not any(code.startswith(sc) for sc in SCORE_RULE_DOC_CODES):
            continue
        note = item.get("note")
        out.append({
            "code": code,
            "name": str(item.get("name") or name_by_code.get(code, code)).strip(),
            "full_score": _num_or_none(item.get("full_score")),
            "estimated_score": _num_or_none(item.get("estimated_score")),
            "note": _truncate(note, _SCORE_NOTE_TRUNC) or "",
        })
    return out


async def refine_with_llm(
    report_input: dict,
    *,
    llm_client=None,
    timeout: float = 180.0,
    meter_usage: bool = True,
) -> Optional[dict]:
    """调用 LLM 生成精简描述/废标判定/得分提取；任何失败返回 None（由上层降级）."""
    from backend.services.usage_recorder import record_llm_usage

    client = llm_client or create_llm_client(timeout=timeout)
    messages = [
        Message(role="system", content=REPORT_SYSTEM_PROMPT),
        Message(role="user", content=build_user_prompt(report_input)),
    ]
    started = time.monotonic()
    try:
        response = await client.generate(messages=messages)
    except Exception as e:
        logger.warning(f"[OverallReport] LLM call failed: {e}")
        if meter_usage:
            try:
                record_llm_usage(
                    response=None,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    status="error",
                    error_message=str(e)[:500],
                )
            except Exception:
                pass
        return None
    if meter_usage:
        try:
            record_llm_usage(
                response=response,
                latency_ms=int((time.monotonic() - started) * 1000),
                status="success",
            )
        except Exception:
            pass
    content = getattr(response, "content", None) or ""
    parsed = _parse_json(content)
    if parsed is None:
        logger.warning(
            "[OverallReport] LLM output is not valid JSON (len=%d), degrading", len(content),
        )
    return parsed


# ---------------------------------------------------------------------------
# 报告组装与评级（纯函数）
# ---------------------------------------------------------------------------

def _fallback_summary(risk_groups: list[dict]) -> str:
    """降级描述：风险项说明/检查项名原文摘录拼接。"""
    parts: list[str] = []
    for g in risk_groups:
        text = (g.get("issue") or g.get("check_item") or "").strip()
        if text:
            parts.append(_truncate(text, _FALLBACK_PART_TRUNC))
    joined = "；".join(parts)
    if not joined:
        joined = f"发现 {len(risk_groups)} 项风险"
    return joined[:_FALLBACK_SUMMARY_MAX] + ("…" if len(joined) > _FALLBACK_SUMMARY_MAX else "")


def compute_rejection_risk(sections: dict[str, list[dict]], llm_output: Optional[dict]) -> tuple[str, str]:
    """废标风险评级（确定性规则）：高=有废标相关严重项；中=有严重/重要但无废标相关；低=仅一般或无风险."""
    critical_entries = sections.get("critical", [])
    rejection_count = sum(
        e["count"] for e in critical_entries if e.get("rejection_related")
    )
    crit_total = sum(e["count"] for e in critical_entries)
    major_total = sum(e["count"] for e in sections.get("major", []))
    minor_total = sum(e["count"] for e in sections.get("minor", []))

    if rejection_count > 0:
        level = "高"
        fallback = f"存在 {rejection_count} 项严重风险直接涉及否决/废标条款"
    elif crit_total + major_total > 0:
        level = "中"
        fallback = f"存在严重/重要风险 {crit_total + major_total} 项，未发现直接触发废标条款的问题"
    elif minor_total > 0:
        level = "低"
        fallback = f"仅存在一般风险 {minor_total} 项"
    else:
        level = "低"
        fallback = "未发现风险项"

    raw = (llm_output or {}).get("rejection_reason")
    reason = _truncate(raw, 200) if isinstance(raw, str) and raw.strip() else ""
    return level, reason or fallback


def assemble_report(
    report_input: dict,
    llm_output: Optional[dict],
    *,
    degraded: bool = False,
    generated_at: Optional[datetime] = None,
) -> dict:
    """组装最终总体报告；LLM 输出逐项校验，缺失/不合法的部分就地降级。"""
    categories = report_input["categories"]
    summaries = _validated_summaries(llm_output or {})
    judgments = _validated_judgments(llm_output or {})

    sections: dict[str, list[dict]] = {sev: [] for sev in SEVERITY_ORDER}
    for cat in categories:
        for sev in SEVERITY_ORDER:
            groups = [g for g in cat["risk_groups"] if g["severity"] == sev]
            if not groups:
                continue
            summary = summaries.get(cat["code"], {}).get(sev) or _fallback_summary(groups)
            entry = {
                "rule_doc": cat["name"],
                "rule_doc_code": cat["code"],
                "count": len(groups),
                "summary": summary,
            }
            if sev == "critical":
                entry["rejection_related"] = bool(
                    cat["rejection_by_rule"]
                    or any(judgments.get(g["id"]) for g in groups)
                )
            sections[sev].append(entry)

    level, reason = compute_rejection_risk(sections, llm_output)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(),
        "degraded": degraded,
        "summary": report_input["summary"],
        "rejection_risk": {"level": level, "reason": reason},
        "risk_sections": sections,
        "score_items": _validated_score_items(llm_output or {}, categories),
    }


# ---------------------------------------------------------------------------
# 编排入口
# ---------------------------------------------------------------------------

async def generate_overall_report(
    task_id: str,
    session_factory,
    *,
    llm_client=None,
    event_callback: Optional[Callable] = None,
    meter_usage: bool = True,
) -> Optional[dict]:
    """为审查任务生成总体报告（不落库，由调用方持久化）.

    Args:
        task_id: ReviewTask.id（= session_id）.
        session_factory: async session factory.
        llm_client: 可注入的 LLM 客户端（测试用）；缺省按 settings 创建.
        event_callback: 可选 SSE 回调 (event_type, data).
        meter_usage: 是否计量 LLM 用量（补生成场景任务用量已 finalize，关掉）.

    Returns:
        报告 dict；任务不存在或组装异常返回 None（尽力而为，不抛异常）.
    """
    from backend.models import Project, ReviewResult, ReviewTask, TodoItem, User

    def _emit(event_type: str, data: dict):
        if event_callback:
            try:
                event_callback(event_type, data)
            except Exception as e:
                logger.warning(f"[OverallReport] event callback failed: {e}")

    try:
        async with session_factory() as db:
            task = (
                await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
            ).scalar_one_or_none()
            if not task:
                logger.warning("[OverallReport] task not found: %s", task_id)
                return None
            project = (
                await db.execute(select(Project).where(Project.id == task.project_id))
            ).scalar_one_or_none()
            todos = (
                await db.execute(
                    select(TodoItem)
                    .where(TodoItem.session_id == task_id)
                    .order_by(TodoItem.created_at.asc())
                )
            ).scalars().all()
            findings = (
                await db.execute(
                    select(ReviewResult)
                    .where(ReviewResult.task_id == task_id)
                    .order_by(ReviewResult.created_at.asc())
                )
            ).scalars().all()
            owner_id = str(project.user_id) if project else ""
            user = None
            if owner_id:
                user = (
                    await db.execute(select(User).where(User.id == owner_id))
                ).scalar_one_or_none()

        report_input = build_report_input(todos, findings)
        _emit("report_llm_started", {"message": "正在汇总生成总体报告"})

        usage_token = None
        if meter_usage:
            try:
                from backend.services.usage_context import (
                    UsageContext,
                    set_usage_context,
                )
                usage_token = set_usage_context(UsageContext(
                    external_user_id=getattr(user, "external_user_id", None),
                    local_user_id=owner_id or None,
                    user_name=(getattr(user, "username", None) or owner_id or "unknown"),
                    enterprise_name=getattr(user, "enterprise_name", None),
                    interior_user=bool(getattr(user, "interior_user", False) or False),
                    project_id=str(task.project_id),
                    task_id=task_id,
                    todo_id=None,
                ))
            except Exception as e:
                logger.warning(f"[OverallReport] set_usage_context failed: {e}")

        try:
            llm_output = await refine_with_llm(
                report_input, llm_client=llm_client, meter_usage=meter_usage,
            )
        finally:
            if usage_token is not None:
                try:
                    from backend.services.usage_context import reset_usage_context
                    reset_usage_context(usage_token)
                except Exception:
                    pass

        degraded = llm_output is None
        report = assemble_report(report_input, llm_output, degraded=degraded)
        logger.info(
            "[OverallReport] generated for task=%s: level=%s, risks=%d, degraded=%s",
            task_id,
            report["rejection_risk"]["level"],
            report["summary"]["risk_item_count"],
            degraded,
        )
        return report
    except Exception as e:
        logger.exception(f"[OverallReport] generation failed for task {task_id}: {e}")
        return None
