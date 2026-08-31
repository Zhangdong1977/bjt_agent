"""标书生成 Agent：招标要素分析 → 章节大纲 → 逐节撰写 → 汇总成文。

直接编排 LLM 调用（不走 Mini-Agent 工具循环）：分析/大纲阶段要求 JSON
输出并由代码规范化校验，章节阶段输出 Markdown；全部调用经
instrument_llm_client 计量，走任务级计费结算。章节产物逐节落库，
支持单节重生成（only_sections）与结果重开页面重插。

写作红线：严禁虚构公司资质、业绩、项目、人员与数据；事实性空白用
"（请补充）"占位，由投标人自行补齐。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import select

from backend.models import BidDraftSection, BidDraftTask
from backend.services.llm_factory import create_llm_client
from backend.services.usage_recorder import instrument_llm_client

from mini_agent.schema import Message

logger = logging.getLogger(__name__)

ANALYSIS_CONTEXT_MAX_CHARS = 120_000
OUTLINE_MAX_NODES = 60
OUTLINE_CONTEXT_MAX_CHARS = 4_000
ANALYSIS_JSON_MAX_CHARS = 8_000
SECTION_RESULT_MAX_CHARS = 24_000
MERMAID_MAX_PER_SECTION = 2

ANALYSIS_SYSTEM_PROMPT = (
    "你是招标文件解析专家，从招标文件文本中提取结构化要素。"
    "只输出一个合法 JSON 对象，不要输出任何其他文字或代码块标记。"
)

ANALYSIS_USER_TEMPLATE = """以下是招标文件文本（可能截断）：

__TENDER_TEXT__

请提取为如下 JSON（提取不到的字段用空字符串或空数组，不要虚构）：

{
  "basic": {"project_name": "", "tendering_unit": "", "opening_date": "", "bid_deadline": "", "budget": ""},
  "tender_requirements": ["招标需求/技术要求要点，每条一句话"],
  "scoring_criteria": ["评分标准要点，每条一句话"],
  "rejection_items": ["废标/否决条款要点，每条一句话"]
}"""

OUTLINE_SYSTEM_PROMPT = (
    "你是资深投标文件编写专家，根据招标要素设计投标文件（标书）章节大纲。"
    "只输出一个合法 JSON 数组，不要输出任何其他文字或代码块标记。"
)

OUTLINE_USER_TEMPLATE = """招标要素提取结果：

__ANALYSIS_JSON__

用户补充要求：
__OUTLINE_HINT__

请输出投标文件章节大纲 JSON 数组（不超过 60 个章节，层级 1-4 级），每项格式：

{"title": "章节标题", "level": 1, "requirement": "本节撰写要求（可选）", "article_count": 2, "text_count": 400}

规则：
1. 大纲应完整覆盖招标需求与评分办法对应的响应内容（技术方案、施工/服务方案、项目管理、人员配置、售后承诺、资格证明文件等），与评分标准呼应；
2. level 为标题层级（1=一级标题），子章节依次加深；
3. article_count 为建议段落数（1-8），text_count 为每段建议字数（100-3000）。"""

SECTION_SYSTEM_PROMPT = (
    "你是资深投标文件编写专家。撰写指定章节的正文，内容专业、具体、结构清晰、可直接用于投标文件。"
    "只输出 Markdown 正文：不要输出本节标题（系统会自动添加），不要输出解释或前言，"
    "不要把整节内容包进代码块围栏（mermaid 图表除外）。"
    "严禁虚构公司的资质、业绩、项目、人员与数据；需要事实性内容时用“（请补充）”占位。"
    "图表运用要求："
    "①对比、参数、人员配置、职责分工、进度安排等结构化内容，优先用 Markdown 表格表达，不要堆砌长句；"
    "②适合图示表达的内容（项目组织架构、实施/服务流程、应急处理流程、进度计划横道图、占比构成等）"
    "用 mermaid 代码块（```mermaid 围栏）输出，语法必须严格合法（flowchart/gantt/pie/timeline 等），"
    "节点与标签使用中文，整段代码块独占成块、前后留空行，每节 mermaid 图最多 2 张；"
    "③图表涉及的名称、日期、数值必须与正文一致，同样严禁虚构；"
    "④纯论述性内容保持文本段落，不要为凑图表而强行图示化。"
)

SECTION_USER_TEMPLATE = """## 项目招标要素（摘要）
__ANALYSIS_JSON__

## 投标文件大纲
__OUTLINE_TEXT__

## 本次撰写章节
标题：__TITLE__
撰写要求：__REQUIREMENT__
篇幅：约 __ARTICLE_COUNT__ 段，每段约 __TEXT_COUNT__ 字。

请撰写该章节正文（Markdown，不含本节标题）。"""


class BidDraftCancelled(RuntimeError):
    """Raised when the user cancels a running bid-draft task."""


def strip_code_fence(text: str) -> str:
    """Remove a whole-output ``` fence if the model added one despite prompts.

    Only bare/markdown fences are stripped: a body that legitimately *is* a
    ```mermaid block must keep its fence (it is a chart, not a wrapper).
    """
    value = (text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            language = lines[0].strip()[3:].strip().lower()
            if language in ("", "markdown", "md"):
                value = "\n".join(lines[1:-1]).strip()
    return value


def parse_json_payload(text: str) -> Any | None:
    """Tolerant JSON extraction: direct load, fence strip, then brace slicing."""
    value = strip_code_fence((text or "").strip())
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        pass
    starts = [index for index in (value.find("{"), value.find("[")) if index >= 0]
    if not starts:
        return None
    start = min(starts)
    end = max(value.rfind("}"), value.rfind("]"))
    if end <= start:
        return None
    try:
        return json.loads(value[start : end + 1])
    except Exception:
        return None


def normalize_outline(nodes: list[Any]) -> list[dict[str, Any]]:
    """Assign stable hierarchical node ids ("1", "1.2") and clamp per-node settings."""
    result: list[dict[str, Any]] = []
    counts: list[int] = []
    for raw in nodes or []:
        if len(result) >= OUTLINE_MAX_NODES:
            break
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()[:200]
        if not title:
            continue
        # LLM titles often repeat the numbering ("1.1 投标函") — node_id already
        # carries it, so strip a leading numeric prefix. "第一章 …" is kept.
        stripped_title = re.sub(r"^\d+(?:\.\d+)*(?:[\s、:：\-—]+|\.\s+)", "", title).strip()
        if stripped_title:
            title = stripped_title
        try:
            level = max(1, min(6, int(raw.get("level") or 1)))
        except (TypeError, ValueError):
            level = 1
        # Keep counters for this level and above; deeper levels reset.
        counts = counts[:level]
        counts += [0] * (level - len(counts))
        counts[level - 1] += 1
        node_id = ".".join(str(item) for item in counts)
        try:
            article_count = max(1, min(8, int(raw.get("article_count") or 2)))
        except (TypeError, ValueError):
            article_count = 2
        try:
            text_count = max(100, min(3_000, int(raw.get("text_count") or 400)))
        except (TypeError, ValueError):
            text_count = 400
        requirement = str(raw.get("requirement") or "").strip()[:2_000] or None
        result.append(
            {
                "node_id": node_id,
                "title": title,
                "level": level,
                "requirement": requirement,
                "article_count": article_count,
                "text_count": text_count,
            }
        )
    return result


def extract_mermaid_fences(text: str) -> list[dict[str, int]]:
    """Locate ```mermaid fenced blocks; returns char ranges including both fences.

    Unterminated fences are ignored (the frontend falls back to code text).
    """
    fences: list[dict[str, int]] = []
    lines = (text or "").splitlines(keepends=True)
    offset = 0
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("```") and stripped[3:].strip().lower() == "mermaid":
            end_offset = offset + len(lines[index])
            closing = index + 1
            while closing < len(lines) and not lines[closing].strip().startswith("```"):
                end_offset += len(lines[closing])
                closing += 1
            if closing >= len(lines):
                break
            closing_line = lines[closing]
            raw_end = end_offset + len(closing_line)
            # Range covers both fences but not the newline after the closing one.
            fences.append({"start": offset, "end": raw_end - (len(closing_line) - len(closing_line.rstrip("\r\n")))})
            offset = raw_end
            index = closing + 1
            continue
        offset += len(lines[index])
        index += 1
    return fences


def clamp_mermaid_blocks(body: str, limit: int = MERMAID_MAX_PER_SECTION) -> str:
    """Cap mermaid charts per section; extra ones become a plain placeholder note."""
    fences = extract_mermaid_fences(body)
    for fence in reversed(fences[limit:]):
        body = body[: fence["start"]] + "（本节图表数量已达上限，多余图示已省略）" + body[fence["end"] :]
    return body


def _bound_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    def _strings(value: Any, limit: int, max_items: int) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:limit] for item in value[:max_items] if str(item).strip()]

    basic_raw = analysis.get("basic") if isinstance(analysis.get("basic"), dict) else {}
    basic = {str(key)[:50]: str(value).strip()[:500] for key, value in list(basic_raw.items())[:30]}
    return {
        "basic": basic,
        "tender_requirements": _strings(analysis.get("tender_requirements"), 1_000, 50),
        "scoring_criteria": _strings(analysis.get("scoring_criteria"), 1_000, 50),
        "rejection_items": _strings(analysis.get("rejection_items"), 1_000, 50),
    }


def _safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.\-]", "_", value) or "section"


def _outline_text(outline: list[dict[str, Any]]) -> str:
    lines = []
    for node in outline:
        indent = "  " * (max(1, int(node.get("level") or 1)) - 1)
        lines.append(f"{indent}- {node.get('title')}")
    return "\n".join(lines)[:OUTLINE_CONTEXT_MAX_CHARS]


class BidDraftAgent:
    """Orchestrates one bid-draft task; the celery wrapper owns final status."""

    def __init__(
        self,
        *,
        task_id: str,
        tender_markdown: str,
        workspace_dir: Path,
        session_factory,
        event_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
        cancel_event: Optional["asyncio.Event"] = None,
        llm_timeout: float = 180.0,
    ) -> None:
        self.task_id = task_id
        self.tender_markdown = tender_markdown or ""
        self.workspace_dir = Path(workspace_dir)
        self.session_factory = session_factory
        self.event_callback = event_callback
        self.cancel_event = cancel_event
        self.client = instrument_llm_client(create_llm_client(timeout=llm_timeout))

    # ------------------------------------------------------------------ utils

    def _publish(self, event_type: str, data: dict[str, Any]) -> None:
        if self.event_callback is not None:
            try:
                self.event_callback(event_type, data or {})
            except Exception:
                logger.exception("bid-draft event publish failed: task=%s", self.task_id)

    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise BidDraftCancelled("用户取消了标书生成任务")

    async def _load_task(self) -> BidDraftTask:
        async with self.session_factory() as db:
            return (
                await db.execute(select(BidDraftTask).where(BidDraftTask.id == self.task_id))
            ).scalar_one()

    async def _update_task(self, **fields: Any) -> None:
        async with self.session_factory() as db:
            task = (
                await db.execute(select(BidDraftTask).where(BidDraftTask.id == self.task_id))
            ).scalar_one()
            for key, value in fields.items():
                setattr(task, key, value)
            await db.commit()

    async def _generate(self, system_prompt: str, user_prompt: str) -> str:
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        response = await self.client.generate(messages=messages)
        return str(getattr(response, "content", "") or "").strip()

    async def _generate_json(self, system_prompt: str, user_prompt: str) -> Any:
        parsed = parse_json_payload(await self._generate(system_prompt, user_prompt))
        if parsed is None:
            raise RuntimeError("模型输出不是合法 JSON，请重试")
        return parsed

    # ---------------------------------------------------------------- phases

    async def _analyze(self) -> dict[str, Any]:
        self._publish("phase", {"phase": "tender_analysis"})
        tender_text = self.tender_markdown[:ANALYSIS_CONTEXT_MAX_CHARS]
        if len(self.tender_markdown) > ANALYSIS_CONTEXT_MAX_CHARS:
            tender_text += "\n…（后文已截断）"
        user_prompt = ANALYSIS_USER_TEMPLATE.replace("__TENDER_TEXT__", tender_text)
        analysis = await self._generate_json(ANALYSIS_SYSTEM_PROMPT, user_prompt)
        if not isinstance(analysis, dict):
            raise RuntimeError("招标要素提取结果不是 JSON 对象")
        analysis = _bound_analysis(analysis)
        await self._update_task(phase="tender_analysis", analysis_result=analysis)
        return analysis

    async def _build_outline(self, analysis: dict[str, Any], hint: str | None) -> list[dict[str, Any]]:
        self._publish("phase", {"phase": "outline"})
        analysis_json = json.dumps(analysis, ensure_ascii=False)[:ANALYSIS_JSON_MAX_CHARS]
        user_prompt = (
            OUTLINE_USER_TEMPLATE.replace("__ANALYSIS_JSON__", analysis_json)
            .replace("__OUTLINE_HINT__", (hint or "无").strip()[:2_000] or "无")
        )
        data = await self._generate_json(OUTLINE_SYSTEM_PROMPT, user_prompt)
        nodes = data if isinstance(data, list) else data.get("outline") if isinstance(data, dict) else None
        outline = normalize_outline(nodes if isinstance(nodes, list) else [])
        if not outline:
            raise RuntimeError("大纲为空，请调整要求后重试")
        await self._update_task(phase="outline", outline=outline)
        return outline

    async def _ensure_section_rows(
        self, outline: list[dict[str, Any]], only_sections: list[str] | None
    ) -> list[dict[str, Any]]:
        wanted = set(only_sections or [])
        scope = [node for node in outline if not wanted or node["node_id"] in wanted]
        if not scope:
            raise RuntimeError("没有需要生成的章节")
        async with self.session_factory() as db:
            existing = set(
                (
                    await db.execute(
                        select(BidDraftSection.node_id).where(BidDraftSection.task_id == self.task_id)
                    )
                ).scalars()
            )
            for node in scope:
                if node["node_id"] not in existing:
                    db.add(
                        BidDraftSection(
                            task_id=self.task_id,
                            node_id=node["node_id"][:200],
                            title=node["title"][:500],
                            status="pending",
                        )
                    )
            await db.commit()
        return scope

    async def _generate_section(
        self, node: dict[str, Any], analysis: dict[str, Any], outline: list[dict[str, Any]]
    ) -> None:
        self._publish(
            "section_started",
            {"node_id": node["node_id"], "title": node["title"]},
        )
        async with self.session_factory() as db:
            row = (
                await db.execute(
                    select(BidDraftSection).where(
                        BidDraftSection.task_id == self.task_id,
                        BidDraftSection.node_id == node["node_id"],
                    )
                )
            ).scalar_one()
            row.status = "generating"
            row.attempts += 1
            await db.commit()

        analysis_json = json.dumps(analysis, ensure_ascii=False)[:ANALYSIS_JSON_MAX_CHARS]
        user_prompt = (
            SECTION_USER_TEMPLATE.replace("__ANALYSIS_JSON__", analysis_json)
            .replace("__OUTLINE_TEXT__", _outline_text(outline))
            .replace("__TITLE__", node["title"])
            .replace("__REQUIREMENT__", node.get("requirement") or "无")
            .replace("__ARTICLE_COUNT__", str(node.get("article_count") or 2))
            .replace("__TEXT_COUNT__", str(node.get("text_count") or 400))
        )
        body = strip_code_fence(await self._generate(SECTION_SYSTEM_PROMPT, user_prompt))
        if not body:
            raise RuntimeError(f"章节「{node['title']}」生成结果为空")
        body = clamp_mermaid_blocks(body)
        level = max(1, min(6, int(node.get("level") or 1)))
        content = f"{'#' * level} {node['title']}\n\n{body}"[:SECTION_RESULT_MAX_CHARS]

        section_path = self.workspace_dir / "sections" / f"{_safe_filename(node['node_id'])}.md"
        section_path.parent.mkdir(parents=True, exist_ok=True)
        section_path.write_text(content, encoding="utf-8")

        word_count = len(re.sub(r"\s", "", content))
        async with self.session_factory() as db:
            row = (
                await db.execute(
                    select(BidDraftSection).where(
                        BidDraftSection.task_id == self.task_id,
                        BidDraftSection.node_id == node["node_id"],
                    )
                )
            ).scalar_one()
            row.status = "generated"
            row.content_path = str(section_path)
            row.word_count = word_count
            row.error_message = None
            await db.commit()
        self._publish(
            "section_completed",
            {"node_id": node["node_id"], "title": node["title"], "word_count": word_count},
        )

    async def _mark_section_failed(self, node_id: str, message: str) -> None:
        try:
            async with self.session_factory() as db:
                row = (
                    await db.execute(
                        select(BidDraftSection).where(
                            BidDraftSection.task_id == self.task_id,
                            BidDraftSection.node_id == node_id,
                        )
                    )
                ).scalar_one()
                row.status = "failed"
                row.error_message = message[:2_000]
                await db.commit()
        except Exception:
            logger.exception("bid-draft section failure mark failed: task=%s node=%s", self.task_id, node_id)

    async def _assemble(self, scope: list[dict[str, Any]]) -> dict[str, Any]:
        self._publish("phase", {"phase": "assembling"})
        parts: list[str] = []
        generated = 0
        failed = 0
        total_words = 0
        async with self.session_factory() as db:
            rows = (
                await db.execute(
                    select(BidDraftSection).where(BidDraftSection.task_id == self.task_id)
                )
            ).scalars()
            by_node = {row.node_id: row for row in rows}
        for node in scope:
            row = by_node.get(node["node_id"])
            if row is None or row.status != "generated" or not row.content_path:
                failed += 1
                continue
            path = Path(row.content_path)
            if not path.is_absolute():
                from backend.config import get_settings

                path = Path(get_settings().workspace_path) / path
            try:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
                generated += 1
                total_words += int(row.word_count or 0)
            except Exception as exc:
                failed += 1
                logger.exception("bid-draft section read failed: task=%s node=%s", self.task_id, node["node_id"])

        assembled_path = self.workspace_dir / "assembled.md"
        assembled_path.parent.mkdir(parents=True, exist_ok=True)
        assembled_path.write_text("\n\n".join(parts), encoding="utf-8")
        return {
            "section_total": len(scope),
            "section_generated": generated,
            "section_failed": failed,
            "word_count": total_words,
            "assembled_path": str(assembled_path),
        }

    # ------------------------------------------------------------------- run

    async def run(self) -> dict[str, Any]:
        task = await self._load_task()
        self._check_cancel()

        analysis = task.analysis_result if isinstance(task.analysis_result, dict) and task.analysis_result else None
        if analysis is None:
            analysis = await self._analyze()
        self._check_cancel()

        options = task.generation_options if isinstance(task.generation_options, dict) else {}
        only_sections = options.get("only_sections") if isinstance(options.get("only_sections"), list) else None

        if isinstance(task.outline, list) and task.outline:
            outline = normalize_outline(task.outline)
        else:
            outline = await self._build_outline(analysis, options.get("outline_hint"))
        self._check_cancel()

        scope = await self._ensure_section_rows(outline, only_sections)
        await self._update_task(phase="generating")
        self._publish("phase", {"phase": "generating", "section_total": len(scope)})

        for node in scope:
            self._check_cancel()
            try:
                await self._generate_section(node, analysis, outline)
            except BidDraftCancelled:
                raise
            except Exception as exc:
                message = str(exc)[:500]
                await self._mark_section_failed(node["node_id"], message)
                self._publish("section_failed", {"node_id": node["node_id"], "error": message})

        self._check_cancel()
        summary = await self._assemble(scope)
        await self._update_task(phase="assembled", summary=summary)
        return summary
