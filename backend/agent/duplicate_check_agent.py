"""One duplicate-check sub-agent bound to exactly two parsed bid documents."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

from backend.schemas.duplicate_check import DuplicateAgentResult
from backend.services.duplicate_retrieval import recall_candidates
from backend.services.duplicate_rule_loader import DuplicateRule
from backend.services.llm_factory import create_llm_client
from backend.agent.tools.duplicate_structure_tools import DuplicateDocumentTocTool, DuplicateSectionContentTool
from backend.utils.mini_agent_utils import setup_mini_agent_path

setup_mini_agent_path()
from mini_agent.schema import Message

logger = logging.getLogger(__name__)


def _extract_json(text: str):
    clean = text.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        start_candidates = [p for p in (clean.find("{"), clean.find("[")) if p >= 0]
        if not start_candidates:
            raise
        start = min(start_candidates)
        for end in range(len(clean), start, -1):
            try:
                return json.loads(clean[start:end])
            except json.JSONDecodeError:
                continue
        raise


class DuplicateCheckAgent:
    def __init__(
        self,
        *,
        document_a: dict,
        document_b: dict,
        rule: DuplicateRule,
        execution_mode: str,
        event_callback: Callable[[str, dict], None] | None = None,
    ):
        self.document_a = document_a
        self.document_b = document_b
        self.rule = rule
        self.execution_mode = execution_mode
        self.event_callback = event_callback
        self.client = create_llm_client(timeout=180.0)
        pair_documents = {document_a["id"]: document_a, document_b["id"]: document_b}
        self.toc_tool = DuplicateDocumentTocTool(pair_documents)
        self.section_tool = DuplicateSectionContentTool(pair_documents)
        self.step = 0
        self.diagnostics: dict = {}

    def _emit(self, event_type: str, data: dict) -> None:
        self.step += 1
        payload = {"step_number": self.step, **data}
        if self.event_callback:
            self.event_callback(event_type, payload)

    async def _call_llm(self, system: str, user: str):
        from backend.services.llm_rate_limiter import acquire_llm_rate_limit
        from backend.services.usage_recorder import record_llm_usage

        started = time.perf_counter()
        try:
            async with acquire_llm_rate_limit():
                async with asyncio.timeout(300):
                    response = await self.client.generate(
                        messages=[Message(role="system", content=system), Message(role="user", content=user)],
                        tools=[],
                    )
            record_llm_usage(
                response=response,
                latency_ms=int((time.perf_counter() - started) * 1000),
                status="success",
            )
            return response
        except Exception as exc:
            record_llm_usage(
                latency_ms=int((time.perf_counter() - started) * 1000),
                status="error",
                error_message=str(exc),
            )
            raise

    def _toc_for_prompt(self, document: dict, structure: dict) -> list[dict]:
        patterns = [re.compile(p, re.IGNORECASE) for p in self.rule.config.exclude_section_title_patterns]
        return [
            {"section_id": s["section_id"], "title": s["title"], "level": s["level"]}
            for s in structure.get("sections", [])
            if not any(pattern.search(s["title"]) for pattern in patterns)
        ]

    async def _structured_material(self) -> tuple[list[dict], dict]:
        toc_result_a = await self.toc_tool.execute(self.document_a["id"])
        toc_result_b = await self.toc_tool.execute(self.document_b["id"])
        if not toc_result_a.success or not toc_result_b.success:
            raise ValueError(toc_result_a.error or toc_result_b.error or "无法读取文档目录")
        structure_a = toc_result_a.data
        structure_b = toc_result_b.data
        toc_a = self._toc_for_prompt(self.document_a, structure_a)
        toc_b = self._toc_for_prompt(self.document_b, structure_b)
        self._emit("duplicate_pair_step", {"message": "正在分析两份文档的章节目录"})
        selection_prompt = f"""你是标书查重子代理。先根据目录选择需要读取并对比的疑似章节对。
不仅要选择同名章节，也要识别标题不同但可能包含相同方案、顺序、人员、设备或专有表述的章节。
最多选择 16 对，宁可覆盖更多合理候选，不要仅凭标题直接认定重复。

文档A：{self.document_a['name']}
目录A：{json.dumps(toc_a, ensure_ascii=False)}

文档B：{self.document_b['name']}
目录B：{json.dumps(toc_b, ensure_ascii=False)}

仅输出 JSON 数组：[{{"section_a_id":"s1","section_b_id":"s2","reason":"..."}}]。"""
        response = await self._call_llm("你是严谨的文档结构分析代理，只输出有效 JSON。", selection_prompt)
        selected = _extract_json(response.content)
        if not isinstance(selected, list):
            raise ValueError("章节选择结果不是数组")
        selected = selected[:16]

        by_a = {s["section_id"]: s for s in structure_a.get("sections", [])}
        by_b = {s["section_id"]: s for s in structure_b.get("sections", [])}
        materials = []
        for item in selected:
            sec_a = by_a.get(item.get("section_a_id"))
            sec_b = by_b.get(item.get("section_b_id"))
            if not sec_a or not sec_b:
                continue
            section_a = await self.section_tool.execute(self.document_a["id"], sec_a["section_id"], max_chars=16000)
            section_b = await self.section_tool.execute(self.document_b["id"], sec_b["section_id"], max_chars=16000)
            if not section_a.success or not section_b.success:
                continue
            content_a = section_a.data["content"]
            content_b = section_b.data["content"]
            materials.append({
                "reason": item.get("reason"),
                "document_a": {"section": sec_a, "content": content_a},
                "document_b": {"section": sec_b, "content": content_b},
            })
        return materials, {"selected_section_pairs": len(materials)}

    async def _retrieval_material(self) -> tuple[list[dict], dict]:
        self._emit("duplicate_pair_step", {"message": "正在切片并召回疑似重复内容"})
        candidates, diagnostics = await recall_candidates(
            self.document_a["parsed_path"], self.document_b["parsed_path"], self.rule.config
        )
        # Keep prompts bounded; ranking scores remain internal and are stripped below.
        materials = []
        for item in candidates[:40]:
            materials.append({
                "candidate_id": item["candidate_id"],
                "recall_type": item["recall_type"],
                "document_a": {**item["document_a"], "excerpt": item["document_a"]["excerpt"][:3000]},
                "document_b": {**item["document_b"], "excerpt": item["document_b"]["excerpt"][:3000]},
            })
        return materials, diagnostics

    async def run(self) -> tuple[DuplicateAgentResult, dict]:
        if self.execution_mode == "structured":
            materials, diagnostics = await self._structured_material()
        else:
            materials, diagnostics = await self._retrieval_material()
        self.diagnostics = diagnostics

        if not materials:
            result = DuplicateAgentResult(
                conclusion="no_suspicious_duplicate",
                summary="未召回需要进一步核验的疑似重复内容。",
                excluded_count=0,
                matches=[],
            )
            return result, diagnostics

        self._emit("duplicate_pair_step", {"message": "正在依据规则核验双边证据"})
        result_prompt = f"""请依据规则核验下面两份投标文件的候选内容，排除法规、固定模板、招标文件引用、行业通用表述等合理重复。
不能输出综合相似度百分比。每条可疑项必须包含两边原文证据；证据 excerpt 必须来自输入，不得编造页码。

规则：
{self.rule.instructions}

文档A：{self.document_a['name']}
文档B：{self.document_b['name']}
执行模式：{self.execution_mode}
候选材料：
{json.dumps(materials, ensure_ascii=False)}

仅输出 JSON 对象：
{{
  "conclusion":"suspicious_duplicate|no_suspicious_duplicate|manual_review_required",
  "summary":"...",
  "excluded_count":0,
  "matches":[{{
    "title":"...",
    "duplicate_type":"exact|near_duplicate|semantic_duplicate",
    "document_a_evidence":{{"section_id":null,"section_title":null,"page_start":null,"page_end":null,"excerpt":"..."}},
    "document_b_evidence":{{"section_id":null,"section_title":null,"page_start":null,"page_end":null,"excerpt":"..."}},
    "analysis":"..."
  }}]
}}"""
        response = await self._call_llm(
            "你是严谨的标书查重核验代理。必须排除合理重复，只输出符合要求的 JSON。",
            result_prompt,
        )
        raw = _extract_json(response.content)
        result = DuplicateAgentResult.model_validate(raw)
        source_a = "\n".join(
            str(item.get("document_a", {}).get("content") or item.get("document_a", {}).get("excerpt") or "")
            for item in materials
        )
        source_b = "\n".join(
            str(item.get("document_b", {}).get("content") or item.get("document_b", {}).get("excerpt") or "")
            for item in materials
        )
        for match in result.matches:
            for evidence, source, label in (
                (match.document_a_evidence.excerpt, source_a, "A"),
                (match.document_b_evidence.excerpt, source_b, "B"),
            ):
                compact = re.sub(r"\s+", "", evidence).strip(".…")
                source_compact = re.sub(r"\s+", "", source)
                if (
                    len(compact) < self.rule.config.min_evidence_chars
                    or not compact
                    or compact not in source_compact
                ):
                    raise ValueError(f"文档{label}证据无法在候选原文中验证")
        self._emit("duplicate_pair_step", {"message": "已完成证据核验"})
        return result, diagnostics


def write_duplicate_report(path: str | Path, *, doc_a: str, doc_b: str, result: DuplicateAgentResult) -> None:
    lines = [
        f"# {doc_a} ↔ {doc_b} 查重结果",
        "",
        f"**结论：** {result.conclusion}",
        "",
        result.summary,
        "",
    ]
    for index, match in enumerate(result.matches, start=1):
        lines.extend([
            f"## {index}. {match.title}", "", match.analysis, "",
            f"### {doc_a}", "", match.document_a_evidence.excerpt, "",
            f"### {doc_b}", "", match.document_b_evidence.excerpt, "",
        ])
    Path(path).write_text("\n".join(lines), encoding="utf-8")
