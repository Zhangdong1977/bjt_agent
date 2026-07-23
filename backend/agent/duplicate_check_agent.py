"""Rule-scoped sub-agent for technical bid duplicate checking."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

from sqlalchemy import select

from backend.agent.tools.duplicate_candidates import DuplicateCandidateSearchTool
from backend.config import get_settings
from backend.models import User
from backend.schemas.duplicate_check import DuplicateFindingPayload
from backend.services.duplicate_candidates import DuplicateCandidateService
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


class DuplicateCheckAgent:
    """Evaluate deterministic candidate pairs against one Markdown rule file."""

    def __init__(
        self,
        *,
        rule_doc_path: str,
        candidate_service: DuplicateCandidateService,
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
        rule_text = Path(self.rule_doc_path).read_text(encoding="utf-8")
        search_tool = DuplicateCandidateSearchTool(self.candidate_service)
        tool_result = await search_tool.execute(rule_text[:1200], limit=40)
        candidates = (tool_result.data or {}).get("candidates", [])

        self._event(
            "sub_agent_step",
            {
                "step_number": 1,
                "step_type": "tool_call",
                "content": "按当前规则检索 A/B 文档候选对",
                "tool_calls": [
                    {"name": search_tool.name, "arguments": {"query": rule_text[:200], "limit": 40}}
                ],
                "tool_results": [
                    {
                        "name": search_tool.name,
                        "result": {
                            "status": "success",
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
                        },
                    }
                ],
            },
        )

        check_items = self._extract_check_items(rule_text)
        if not candidates:
            return [], check_items

        prompt = self._build_prompt(rule_text, candidates)
        messages = [
            Message(
                role="system",
                content=(
                    "你是技术应标书查重子代理。只能依据给出的规则和候选证据判断。"
                    "相似度和双方原文由工具确定，不得编造。只输出 JSON。"
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
                    parsed = self._parse_response(response.content)
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
        findings = self._materialize_findings(parsed, candidates)

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
        parsed: list[dict], candidates: list[dict]
    ) -> list[DuplicateFindingPayload]:
        """Bind model decisions to immutable candidate evidence and scores."""
        by_id = {item["candidate_id"]: item for item in candidates}
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
            findings.append(
                DuplicateFindingPayload(
                    check_item_name=str(item.get("check_item_name") or "规则查重"),
                    verdict=str(item.get("verdict") or "suspicious"),
                    similarity_score=float(source["similarity_score"]),
                    match_type=match_type,
                    left_excerpt=source["left_excerpt"],
                    left_location=source["left_location"],
                    right_excerpt=source["right_excerpt"],
                    right_location=source["right_location"],
                    explanation=str(item.get("explanation") or "双方内容高度相似"),
                    suggestion=(str(item["suggestion"]) if item.get("suggestion") else None),
                    evidence={
                        "candidate_id": candidate_id,
                        "lexical_score": source.get("lexical_score"),
                        "structure_score": source.get("structure_score"),
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
        names = re.findall(r"^###\s+(?:检查项\d+[：:]?)?\s*(.+)$", rule_text, re.M)
        return [
            {"id": f"item-{idx}", "title": name.strip()}
            for idx, name in enumerate(names, 1)
            if name.strip()
        ] or [{"id": "item-1", "title": "规则查重"}]

    @staticmethod
    def _parse_response(content: str) -> list[dict]:
        text = content.strip()
        fenced = _FENCE_RE.search(text)
        if fenced:
            text = fenced.group(1).strip()
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("findings", [])
        if not isinstance(data, list):
            raise ValueError("输出必须是 JSON 数组")
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("结果项必须是对象")
            if item.get("verdict") not in {"reasonable", "suspicious"}:
                raise ValueError("verdict 必须为 reasonable 或 suspicious")
        return data

    @staticmethod
    def _build_prompt(rule_text: str, candidates: list[dict]) -> str:
        return f"""请依据下列规则检查候选对。

【规则文件】
{rule_text}

【工具候选】
{json.dumps(candidates, ensure_ascii=False)}

输出要求：
1. 只输出与本规则有关、确有判断价值的候选；最多 20 条。
2. 合理重复输出 verdict=reasonable，疑似不合理重复输出 verdict=suspicious。
3. candidate_id 必须来自工具候选，不得修改相似度、原文或位置。
4. match_type 只能为 exact、near_exact、semantic、structural、ocr_error、logic_anomaly。
5. 不得作出串标、围标等确定性法律结论。

严格输出 JSON 数组：
[
  {{
    "candidate_id": "候选ID",
    "check_item_name": "检查项名称",
    "verdict": "reasonable 或 suspicious",
    "match_type": "类型",
    "explanation": "结合规则和双方证据的判断理由",
    "suggestion": "可选处理建议"
  }}
]
没有相关结果时输出 []。"""
