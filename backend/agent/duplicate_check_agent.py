"""Rule-scoped sub-agent for technical bid duplicate checking."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Callable

from sqlalchemy import select

from backend.agent.tools.duplicate_candidates import (
    DuplicateCandidateContextTool,
    DuplicateCandidateSearchTool,
    DuplicateSourceContextTool,
    DuplicateSourceSearchTool,
)
from backend.config import get_settings
from backend.models import User
from backend.schemas.duplicate_check import DuplicateFindingPayload
from backend.services.duplicate_candidates import DuplicateCandidateService
from backend.services.duplicate_rules import (
    build_rule_query,
    extract_check_items as extract_rule_check_items,
    filter_candidate_payloads,
    load_duplicate_rule,
)
from backend.services.duplicate_sources import DuplicateSourceIndex
from backend.services.llm_factory import create_llm_client
from backend.services.usage_context import (
    UsageContext,
    reset_usage_context,
    set_usage_context,
)
from backend.services.usage_recorder import record_llm_usage
from backend.utils.mini_agent_utils import setup_mini_agent_path

setup_mini_agent_path()
from mini_agent.schema import Message  # noqa: E402

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.I | re.S)
_ALLOWED_MATCH_TYPES = {
    "exact",
    "near_exact",
    "semantic",
    "structural",
    "ocr_error",
    "logic_anomaly",
}
_ALLOWED_SOURCE_BASES = {"tender", "public", "bidder_authored", "unknown"}
_SOURCE_DEPENDENT_RE = re.compile(
    r"(?:非招标|不属于招标|招标(?:文件|要求)?[^。；\n]{0,30}(?:未|没有|不含)|非强制|强制性要求|国家标准|法律规定|公开资料|公共规范)",
    re.I,
)
_LEGAL_CONCLUSION_RE = re.compile(
    r"(?:确定|确认|足以证明|可以认定|应认定|构成|属于)[^。；\n]{0,16}(?:串标|围标|陪标|违法)",
    re.I,
)


class DuplicateCheckAgent:
    """Evaluate deterministic candidate pairs against one Markdown rule file."""

    def __init__(
        self,
        *,
        rule_doc_path: str,
        candidate_service: DuplicateCandidateService,
        source_index: DuplicateSourceIndex | None = None,
        task_id: str,
        todo_id: str,
        project_id: str,
        user_id: str,
        session_factory,
        event_callback: Callable[[str, dict], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ):
        self.rule_doc_path = rule_doc_path
        self.candidate_service = candidate_service
        self.source_index = source_index
        self.task_id = task_id
        self.todo_id = todo_id
        self.project_id = project_id
        self.user_id = user_id
        self.session_factory = session_factory
        self.event_callback = event_callback
        self.cancel_event = cancel_event or asyncio.Event()
        self.rule_doc_name = Path(rule_doc_path).name

    def _event(self, event_type: str, data: dict) -> None:
        if self.event_callback:
            self.event_callback(event_type, {"todo_id": self.todo_id, **data})

    async def _set_usage_context(self):
        async with self.session_factory() as db:
            user = (await db.execute(select(User).where(User.id == self.user_id))).scalar_one_or_none()
        return set_usage_context(
            UsageContext(
                external_user_id=user.external_user_id if user else None,
                local_user_id=self.user_id,
                user_name=(user.username if user else self.user_id) or self.user_id,
                enterprise_name=user.enterprise_name if user else None,
                interior_user=bool(user.interior_user) if user else False,
                project_id=self.project_id,
                task_id=self.task_id,
                todo_id=self.todo_id,
            )
        )

    async def run(self) -> tuple[list[DuplicateFindingPayload], list[dict]]:
        if self.cancel_event.is_set():
            raise asyncio.CancelledError()
        rule = load_duplicate_rule(self.rule_doc_path)
        rule_text = rule.body
        query = build_rule_query(rule)
        search_tool = DuplicateCandidateSearchTool(self.candidate_service)
        tool_result = await search_tool.execute(query, limit=rule.max_candidates)
        raw_candidates = (tool_result.data or {}).get("candidates", [])
        candidates = filter_candidate_payloads(rule, raw_candidates)
        context_tool = DuplicateCandidateContextTool(self.candidate_service)
        context_count = 0
        enriched_candidates: list[dict] = []
        # Context retrieval is deterministic and bounded: one local lookup per
        # already-selected candidate, with no open-ended agent/tool loop.
        for index, candidate in enumerate(candidates):
            enriched = dict(candidate)
            # The ranked top slice gets context.  The per-rule budget keeps
            # prompts bounded while allowing table/image-heavy rules more room.
            if index < rule.context_candidates:
                context_result = await context_tool.execute(candidate["candidate_id"])
                if context_result.success and context_result.data:
                    enriched["left_context"] = context_result.data.get("left_context", {})
                    enriched["right_context"] = context_result.data.get("right_context", {})
                    context_count += 1
            enriched_candidates.append(enriched)
        candidates = enriched_candidates
        source_candidates: list[dict] = []
        source_context_count = 0
        source_search_calls: list[dict] = []
        if (
            self.source_index is not None
            and rule.source_candidates > 0
            and rule.source_bases
        ):
            source_search_tool = DuplicateSourceSearchTool(self.source_index)
            per_basis_limit = max(
                1,
                math.ceil(rule.source_candidates / len(rule.source_bases)),
            )
            seen_source_ids: set[str] = set()
            for source_basis in rule.source_bases:
                remaining = rule.source_candidates - len(source_candidates)
                if remaining <= 0:
                    break
                request_limit = min(per_basis_limit, remaining)
                source_result = await source_search_tool.execute(
                    query,
                    source_basis=source_basis,
                    limit=request_limit,
                )
                source_search_calls.append(
                    {
                        "name": source_search_tool.name,
                        "arguments": {
                            "query": query,
                            "source_basis": source_basis,
                            "limit": request_limit,
                        },
                    }
                )
                for source in (source_result.data or {}).get("sources", []):
                    source_id = str(source.get("source_reference_id") or "")
                    if not source_id or source_id in seen_source_ids:
                        continue
                    seen_source_ids.add(source_id)
                    source_candidates.append(source)
            source_candidates.sort(
                key=lambda item: float(item.get("retrieval_score", 0.0) or 0.0),
                reverse=True,
            )
            source_candidates = source_candidates[: rule.source_candidates]
            source_context_tool = DuplicateSourceContextTool(self.source_index)
            enriched_sources: list[dict] = []
            for index, source in enumerate(source_candidates):
                enriched = dict(source)
                if index < rule.source_context_candidates:
                    context_result = await source_context_tool.execute(
                        source["source_reference_id"]
                    )
                    if context_result.success and context_result.data:
                        enriched["context"] = context_result.data.get("context", {})
                        source_context_count += 1
                enriched_sources.append(enriched)
            source_candidates = enriched_sources

        self._event(
            "sub_agent_step",
            {
                "step_number": 1,
                "step_type": "tool_call",
                "content": "按当前规则检索 A/B 文档候选对",
                "tool_calls": [
                    {
                        "name": search_tool.name,
                        "arguments": {
                            "query": query,
                            "limit": rule.max_candidates,
                        },
                    },
                    {
                        "name": "filter_duplicate_candidates_by_rule",
                        "arguments": {
                            "candidate_types": list(rule.candidate_types),
                            "channels": list(rule.channels),
                            "min_evidence_strength": rule.min_evidence_strength,
                        },
                    },
                    *(
                        [
                            {
                                "name": context_tool.name,
                                "arguments": {
                                    "radius": 1,
                                    "candidate_count": len(candidates),
                                    "context_budget": rule.context_candidates,
                                },
                            }
                        ]
                        if candidates
                        else []
                    ),
                    *source_search_calls,
                    *(
                        [
                            {
                                "name": "get_duplicate_source_context",
                                "arguments": {
                                    "radius": 1,
                                    "source_count": len(source_candidates),
                                    "context_budget": rule.source_context_candidates,
                                },
                            }
                        ]
                        if source_candidates and rule.source_context_candidates > 0
                        else []
                    ),
                ],
                "tool_results": [
                    {
                        "name": search_tool.name,
                        "result": {
                            "status": "success",
                            "rule_id": rule.rule_id,
                            "rule_version": rule.version,
                            "raw_count": len(raw_candidates),
                            "count": len(candidates),
                            "candidates": [
                                {
                                    "candidate_id": item["candidate_id"],
                                    "similarity_score": item["similarity_score"],
                                    "match_type": item["match_type"],
                                    "left_location": item["left_location"],
                                    "right_location": item["right_location"],
                                }
                                for item in candidates
                            ],
                            "context_count": context_count,
                            "source_count": len(source_candidates),
                            "source_context_count": source_context_count,
                        },
                    },
                    {
                        "name": "filter_duplicate_candidates_by_rule",
                        "result": {
                            "status": "success",
                            "raw_count": len(raw_candidates),
                            "selected_count": len(candidates),
                            "candidate_types": list(rule.candidate_types),
                            "channels": list(rule.channels),
                            "min_evidence_strength": rule.min_evidence_strength,
                        },
                    },
                ],
            },
        )

        check_items = [
            {"id": item["id"], "title": item["title"]}
            for item in rule.check_items
        ]
        if not candidates:
            return [], check_items

        prompt = self._build_prompt(
            rule_text,
            candidates,
            source_candidates,
            check_items=check_items,
            candidate_types=rule.candidate_types,
            channels=rule.channels,
        )
        messages = [
            Message(
                role="system",
                content=(
                    "你是技术应标书查重子代理。只能依据给出的规则和候选证据判断。"
                    "相似度、双方原文、位置和前后文由工具确定，不得编造。"
                    "没有招标文件或公开来源证据时，不得断言非招标强制、国家标准或法律强制要求。只输出 JSON。"
                ),
            ),
            Message(role="user", content=prompt),
        ]

        usage_token = await self._set_usage_context()
        try:
            parsed: list[dict] | None = None
            last_error: Exception | None = None
            llm_client = create_llm_client(timeout=180)
            for attempt in range(2):
                started = time.perf_counter()
                try:
                    response = await self._generate_with_cancellation(llm_client, messages)
                    latency = int((time.perf_counter() - started) * 1000)
                    record_llm_usage(response=response, latency_ms=latency, status="success")
                    parsed = self._parse_response(
                        response.content,
                        allowed_check_items=check_items,
                    )
                    break
                except Exception as exc:
                    latency = int((time.perf_counter() - started) * 1000)
                    record_llm_usage(
                        latency_ms=latency,
                        status="error",
                        error_message=str(exc),
                    )
                    last_error = exc
                    if attempt == 0:
                        messages.append(
                            Message(
                                role="user",
                                content="上一次输出无法通过 JSON 校验。请严格按要求重新输出 JSON 数组。",
                            )
                        )
            if parsed is None:
                raise ValueError(f"子代理结构化输出失败：{last_error}")
        finally:
            reset_usage_context(usage_token)

        self._event(
            "sub_agent_llm_output",
            {
                "step": 2,
                "content": json.dumps(parsed, ensure_ascii=False),
                "tool_calls": [],
            },
        )
        findings = self._materialize_findings(parsed, candidates, source_candidates)

        self._event(
            "sub_agent_step",
            {
                "step_number": 2,
                "step_type": "final",
                "content": f"规则判断完成，输出 {len(findings)} 条查重结果",
                "tool_results": [
                    {
                        "name": "duplicate_rule_decision",
                        "result": {
                            "status": "success",
                            "findings": [item.model_dump() for item in findings],
                        },
                    }
                ],
            },
        )
        return findings, check_items

    @staticmethod
    def _materialize_findings(
        parsed: list[dict],
        candidates: list[dict],
        source_candidates: list[dict] | None = None,
    ) -> list[DuplicateFindingPayload]:
        """Bind model decisions to immutable candidate evidence and scores."""
        by_id = {item["candidate_id"]: item for item in candidates}
        source_by_id = {
            item["source_reference_id"]: item
            for item in (source_candidates or [])
            if item.get("source_reference_id")
        }
        findings: list[DuplicateFindingPayload] = []
        seen: set[str] = set()
        for item in parsed[:20]:
            candidate_id = str(item.get("candidate_id", ""))
            source = by_id.get(candidate_id)
            if not source or candidate_id in seen:
                continue
            seen.add(candidate_id)
            match_type = str(item.get("match_type") or source["match_type"])
            if match_type not in _ALLOWED_MATCH_TYPES:
                match_type = source["match_type"]
            explanation = str(item.get("explanation") or "双方内容高度相似")
            suggestion = str(item["suggestion"]) if item.get("suggestion") else None
            candidate_basis = str(source.get("source_basis") or "unknown")
            if candidate_basis not in _ALLOWED_SOURCE_BASES:
                candidate_basis = "unknown"
            has_explicit_source_basis = "source_basis" in item
            requested_basis = str(item.get("source_basis") or candidate_basis)
            if requested_basis not in _ALLOWED_SOURCE_BASES:
                requested_basis = "unknown"
            # A model cannot promote bidder-authored excerpts to tender/public
            # evidence.  Keep the audit trail explicit and downgrade
            # source-dependent claims to ``unknown`` when their basis is absent.
            source_basis = requested_basis
            verdict = str(item.get("verdict") or "unknown")
            if has_explicit_source_basis and requested_basis == "unknown":
                verdict = "unknown"
            source_reference_id = str(item.get("source_reference_id") or "")
            source_reference = source_by_id.get(source_reference_id)
            if requested_basis in {"tender", "public"}:
                source_is_traceable = bool(
                    source_reference
                    and source_reference.get("source_basis") == requested_basis
                    and source_reference.get("source_document_id")
                    and source_reference.get("source_block_id")
                    and source_reference.get("source_snapshot_hash")
                    and source_reference.get("source_version")
                )
                if not source_is_traceable:
                    source_basis = "unknown"
                    verdict = "unknown"
            if (
                _SOURCE_DEPENDENT_RE.search(f"{explanation} {suggestion or ''}")
                and source_basis not in {"tender", "public"}
            ):
                source_basis = "unknown"
                verdict = "unknown"
            findings.append(
                DuplicateFindingPayload(
                    check_item_name=str(item.get("check_item_name") or "规则查重"),
                    verdict=verdict,
                    source_basis=source_basis,
                    similarity_score=float(source["similarity_score"]),
                    match_type=match_type,
                    left_excerpt=source["left_excerpt"],
                    left_location=source["left_location"],
                    right_excerpt=source["right_excerpt"],
                    right_location=source["right_location"],
                    explanation=explanation,
                    suggestion=suggestion,
                    evidence={
                        "candidate_id": candidate_id,
                        "left_document_id": source.get("left_document_id"),
                        "right_document_id": source.get("right_document_id"),
                        "left_block_id": source.get("left_block_id"),
                        "right_block_id": source.get("right_block_id"),
                        "lexical_score": source.get("lexical_score"),
                        "structure_score": source.get("structure_score"),
                        "semantic_score": source.get("semantic_score"),
                        "image_score": source.get("image_score"),
                        "table_comparison": source.get("table_comparison"),
                        "image_comparison": source.get("image_comparison"),
                        "evidence_strength": source.get("evidence_strength"),
                        "normalized_length": source.get("normalized_length"),
                        "left_occurrences": source.get("left_occurrences"),
                        "right_occurrences": source.get("right_occurrences"),
                        "source_basis": source_basis,
                        "source_evidence": candidate_basis,
                        "source_reference": source_reference,
                    },
                )
            )
        return findings

    async def _generate_with_cancellation(self, llm_client, messages):
        """Stop waiting for an in-flight provider call once the task is cancelled."""
        if self.cancel_event.is_set():
            raise asyncio.CancelledError()
        generate_task = asyncio.create_task(llm_client.generate(messages=messages))
        cancel_task = asyncio.create_task(self.cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                {generate_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_task in done and generate_task not in done:
                generate_task.cancel()
                await asyncio.gather(generate_task, return_exceptions=True)
                raise asyncio.CancelledError()
            return await generate_task
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)

    @staticmethod
    def _extract_check_items(rule_text: str) -> list[dict]:
        items = extract_rule_check_items(rule_text)
        if items:
            return [{"id": item["id"], "title": item["title"]} for item in items]
        names = re.findall(r"^###\s+(.+)$", rule_text, re.M)
        return [
            {"id": f"item-{idx}", "title": name.strip()}
            for idx, name in enumerate(names, 1)
            if name.strip()
        ] or [{"id": "item-1", "title": "规则查重"}]

    @staticmethod
    def _parse_response(
        content: str,
        *,
        allowed_check_items: list[dict] | None = None,
    ) -> list[dict]:
        text = content.strip()
        fenced = _FENCE_RE.search(text)
        if fenced:
            text = fenced.group(1).strip()
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("findings", [])
        if not isinstance(data, list):
            raise ValueError("输出必须是 JSON 数组")
        allowed_titles = {
            str(item.get("title") or "")
            for item in (allowed_check_items or [])
            if item.get("title")
        }
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("结果项必须是对象")
            if item.get("verdict") not in {"reasonable", "suspicious", "unknown"}:
                raise ValueError("verdict 必须为 reasonable、suspicious 或 unknown")
            if allowed_titles and str(item.get("check_item_name") or "") not in allowed_titles:
                raise ValueError("check_item_name 必须来自当前规则的检查项")
            if _LEGAL_CONCLUSION_RE.search(
                f"{item.get('explanation') or ''} {item.get('suggestion') or ''}"
            ):
                raise ValueError("结果说明不得作出串标、围标或违法的确定性法律结论")
        return data

    @staticmethod
    def _build_prompt(
        rule_text: str,
        candidates: list[dict],
        source_candidates: list[dict] | None = None,
        *,
        check_items: list[dict] | None = None,
        candidate_types: tuple[str, ...] | None = None,
        channels: tuple[str, ...] | None = None,
    ) -> str:
        item_payload = check_items or []
        routing_payload = {
            "candidate_types": list(candidate_types or ()),
            "channels": list(channels or ()),
        }
        return f"""请依据下列规则检查候选对。

【规则文件】
{rule_text}

【工具候选】
{json.dumps(candidates, ensure_ascii=False)}

【本规则检查项】
{json.dumps(item_payload, ensure_ascii=False)}

【本规则检索约束】
{json.dumps(routing_payload, ensure_ascii=False)}

【已固化来源候选】
{json.dumps(source_candidates or [], ensure_ascii=False)}

输出要求：
1. 只输出与本规则有关、确有判断价值的候选；最多 20 条；check_item_name 必须来自本规则检查项。
2. 合理重复输出 verdict=reasonable，疑似不合理重复输出 verdict=suspicious；证据不足或依赖未提供的招标/公开来源时输出 verdict=unknown。
3. candidate_id 必须来自工具候选，不得修改相似度、原文或位置。
4. match_type 只能为 exact、near_exact、semantic、structural、ocr_error、logic_anomaly。
5. 不得作出串标、围标等确定性法律结论。
6. source_basis 只能为 tender、public、bidder_authored、unknown。填写 tender/public 时必须同时填写上面来源候选中真实存在且同类型的 source_reference_id；无具体来源文档 ID、原文位置、快照 hash 或版本时必须填写 unknown。
7. 来源证据只能解释重复为何可能合理，不能覆盖内部代号、错误型号、异常单位等 A/B 独立风险。

严格输出 JSON 数组：
[
  {{
    "candidate_id": "候选ID",
    "check_item_name": "检查项名称",
    "verdict": "reasonable、suspicious 或 unknown",
    "source_basis": "tender、public、bidder_authored 或 unknown",
    "source_reference_id": "仅 tender/public 时填写来源候选ID",
    "match_type": "类型",
    "explanation": "结合规则和双方证据的判断理由",
    "suggestion": "可选处理建议"
  }}
]
没有相关结果时输出 []。"""
