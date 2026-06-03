"""CaseExtractor: compress AgentStep trajectory into an ExperienceCase record."""

import json
import logging
import re

from mini_agent.schema import Message

from backend.services.llm_factory import create_llm_client
from backend.experience.prompts.case_filter_zh import CASE_FILTER_PROMPT
from backend.experience.prompts.case_compress_zh import CASE_COMPRESS_PROMPT

logger = logging.getLogger(__name__)


class CaseExtractor:
    def __init__(self, llm_client=None):
        self._llm_client = llm_client or create_llm_client()

    async def extract(
        self,
        task_id: str,
        project_id: str,
        rule_doc_name: str,
        group_id: str,
        user_id: str,
        agent_steps: list,
        findings: list,
    ) -> dict | None:
        if not agent_steps or (not findings and len(agent_steps) < 3):
            logger.info(f"Skipping case extraction for task {task_id}: insufficient data")
            return None

        should_extract = await self._filter_case(agent_steps, findings)
        if not should_extract:
            logger.info(f"Case filtered out for task {task_id}")
            return None

        compressed = await self._compress_case(agent_steps, findings, rule_doc_name)
        if not compressed:
            logger.warning(f"Case compression failed for task {task_id}")
            return None

        quality_score_eval = await self._evaluate_quality(findings)

        quality_score = 0.6 * quality_score_eval + 0.4 * compressed.get("quality_score_llm", 0.5)
        quality_score = max(0.0, min(1.0, quality_score))

        severity_parts = []
        for f in findings[:5]:
            sev = f.get("severity", "unknown")
            if sev and sev not in severity_parts:
                severity_parts.append(sev)
        severity_summary = "、".join(severity_parts) if severity_parts else "问题"

        task_intent = compressed.get("task_intent") or f"审查 {rule_doc_name}，发现 {len(findings)} 项{severity_summary}"

        return {
            "task_id": task_id,
            "project_id": project_id,
            "rule_doc_name": rule_doc_name,
            "group_id": group_id,
            "user_id": user_id,
            "task_intent": task_intent,
            "approach": compressed.get("approach", ""),
            "key_insight": compressed.get("key_insight"),
            "quality_score_llm": compressed.get("quality_score_llm", 0.5),
            "quality_score_eval": quality_score_eval,
            "quality_score": quality_score,
            "finding_count": len(findings),
            "finding_ids": [f.get("id") or f.get("finding_id") for f in findings if f.get("id") or f.get("finding_id")],
            "raw_step_count": len(agent_steps),
            "compressed_step_count": compressed.get("compressed_step_count", 1),
            "metadata": {
                "model": getattr(self._llm_client, "model", None),
            },
        }

    async def _filter_case(self, agent_steps: list, findings: list) -> bool:
        steps_summary = self._summarize_steps(agent_steps)
        findings_summary = f"发现 {len(findings)} 项问题" if findings else "未发现问题"

        prompt = CASE_FILTER_PROMPT.format(
            steps_summary=steps_summary[:2000],
            findings_summary=findings_summary,
            step_count=len(agent_steps),
            finding_count=len(findings),
        )

        try:
            messages = [
                Message(role="system", content="你是审查经验提取评估器。仅输出 JSON。"),
                Message(role="user", content=prompt),
            ]
            response = await self._llm_client.generate(messages=messages)
            result = self._parse_json(response.content)
            return result.get("should_extract", True)
        except Exception as e:
            logger.warning(f"Case filter LLM call failed: {e}, defaulting to extract=True")
            return True

    async def _compress_case(self, agent_steps: list, findings: list, rule_doc_name: str) -> dict | None:
        steps_text = self._summarize_steps(agent_steps)
        findings_text = self._summarize_findings(findings)

        prompt = CASE_COMPRESS_PROMPT.format(
            rule_doc_name=rule_doc_name,
            agent_steps=steps_text[:4000],
            findings=findings_text[:2000],
        )

        try:
            messages = [
                Message(role="system", content="你是审查经验压缩器。仅输出 JSON。"),
                Message(role="user", content=prompt),
            ]
            response = await self._llm_client.generate(messages=messages)
            return self._parse_json(response.content)
        except Exception as e:
            logger.warning(f"Case compression LLM call failed: {e}")
            return None

    async def _evaluate_quality(self, findings: list) -> float:
        if not findings:
            return 0.3
        try:
            from backend.agent.quality_evaluation import QualityEvaluator
            evaluator = QualityEvaluator()
            eval_result = await evaluator.evaluate_findings_batch(findings)
            return eval_result.overall_quality_score / 100.0
        except Exception as e:
            logger.warning(f"Quality evaluation failed: {e}, using default")
            return 0.5

    def _summarize_steps(self, agent_steps: list) -> str:
        parts = []
        for step in agent_steps:
            if isinstance(step, dict):
                step_type = step.get("step_type", "unknown")
                content = step.get("content", "")
                tool_name = step.get("tool_name")
                if tool_name:
                    tool_args = step.get("tool_args", {})
                    args_str = json.dumps(tool_args, ensure_ascii=False)[:200] if tool_args else ""
                    parts.append(f"[{step_type}] {tool_name}({args_str})")
                else:
                    parts.append(f"[{step_type}] {content[:200]}")
            else:
                step_type = getattr(step, "step_type", "unknown")
                content = getattr(step, "content", "")
                tool_name = getattr(step, "tool_name", None)
                if tool_name:
                    tool_args = getattr(step, "tool_args", {}) or {}
                    args_str = json.dumps(tool_args, ensure_ascii=False)[:200] if tool_args else ""
                    parts.append(f"[{step_type}] {tool_name}({args_str})")
                else:
                    parts.append(f"[{step_type}] {content[:200]}")
        return "\n".join(parts)

    def _summarize_findings(self, findings: list) -> str:
        parts = []
        for f in findings[:10]:
            if isinstance(f, dict):
                key = f.get("requirement_key", "?")
                compliant = f.get("is_compliant", "?")
                severity = f.get("severity", "?")
                parts.append(f"- [{severity}] {key}: is_compliant={compliant}")
            else:
                key = getattr(f, "requirement_key", "?")
                compliant = getattr(f, "is_compliant", "?")
                severity = getattr(f, "severity", "?")
                parts.append(f"- [{severity}] {key}: is_compliant={compliant}")
        return "\n".join(parts)

    def _parse_json(self, content: str) -> dict:
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            pass
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        json_match = re.search(r'\{[^{}]*"[^}]+\}[^{}]*\}', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        return {}
