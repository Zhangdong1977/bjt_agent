"""ExperienceRetriever: retrieve relevant experience skills for a review task."""

import json
import logging
import re

from mini_agent.schema import Message

from backend.config import get_settings
from backend.services.llm_factory import create_llm_client
from backend.experience.prompts.skill_relevance_verify_zh import SKILL_RELEVANCE_VERIFY_PROMPT

logger = logging.getLogger(__name__)


class ExperienceRetriever:
    def __init__(self, llm_client=None):
        self._llm_client = llm_client or create_llm_client()
        self._settings = get_settings()

    async def retrieve(
        self,
        group_id: str,
        query: str,
        doc_meta: dict | None = None,
    ) -> list[dict]:
        candidates = await self._sql_query(group_id)
        if not candidates:
            return []

        rag_candidates = await self._rag_recall(group_id, query)
        if rag_candidates:
            seen_ids = {c["id"] for c in candidates}
            for rc in rag_candidates:
                if rc["id"] not in seen_ids:
                    candidates.append(rc)
                    seen_ids.add(rc["id"])

        verified = await self._llm_verify(candidates, doc_meta)
        if verified is not None:
            return verified

        return self._fallback_rank(candidates)

    async def _sql_query(self, group_id: str) -> list[dict]:
        from backend.models.base import async_session_factory
        from backend.experience.models import ExperienceSkill
        from sqlalchemy import select

        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(ExperienceSkill).where(
                        ExperienceSkill.group_id == group_id,
                        ExperienceSkill.retired_at.is_(None),
                        ExperienceSkill.maturity_score >= self._settings.experience_maturity_threshold,
                        ExperienceSkill.confidence >= self._settings.experience_confidence_retire,
                    ).order_by(
                        (ExperienceSkill.maturity_score * ExperienceSkill.confidence).desc()
                    ).limit(10)
                )
                skills = result.scalars().all()
                return [self._skill_to_dict(s) for s in skills]
        except Exception as e:
            logger.warning(f"SQL query for experience skills failed: {e}")
            return []

    async def _rag_recall(self, group_id: str, query: str) -> list[dict] | None:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._settings.rag_memory_service_url}/api/search",
                    json={"query": query, "limit": 10},
                    headers={"X-User-ID": "experience_system"},
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                results = []
                for item in data.get("results", []):
                    path = item.get("path", "")
                    if "experience_skill" in path and group_id in path:
                        results.append({
                            "id": path.split("/")[-1] if "/" in path else path,
                            "name": item.get("snippet", "")[:100],
                            "content": item.get("snippet", ""),
                            "relevance_score": item.get("score", 0.5),
                        })
                return results[:10]
        except Exception as e:
            logger.warning(f"RAG recall failed: {e}")
            return None

    async def _llm_verify(self, candidates: list[dict], doc_meta: dict | None) -> list[dict] | None:
        if not candidates:
            return []

        max_inject = self._settings.experience_max_inject
        candidates_text = ""
        for i, c in enumerate(candidates[:10], 1):
            candidates_text += (
                f"技能 {i} (ID: {c['id']}):\n"
                f"  名称: {c.get('name', '')}\n"
                f"  描述: {c.get('description', '')}\n"
                f"  内容摘要: {c.get('content', '')[:200]}\n\n"
            )

        meta_text = ""
        if doc_meta:
            for k, v in doc_meta.items():
                meta_text += f"- {k}: {v}\n"
        if not meta_text:
            meta_text = "- （未提供元信息）"

        prompt = SKILL_RELEVANCE_VERIFY_PROMPT.format(
            doc_meta=meta_text,
            candidate_skills=candidates_text,
        )

        try:
            messages = [
                Message(role="system", content="你是审查经验相关性校验器。仅输出 JSON。"),
                Message(role="user", content=prompt),
            ]
            response = await self._llm_client.generate(messages=messages)
            result = self._parse_json(response.content)

            verified_items = result.get("skills", [])
            if not isinstance(verified_items, list):
                return None

            id_to_candidate = {c["id"]: c for c in candidates}
            verified = []
            for item in verified_items:
                skill_id = item.get("skill_id")
                relevance = item.get("relevance_score", 0.0)
                if skill_id in id_to_candidate and relevance >= 0.6:
                    candidate = id_to_candidate[skill_id]
                    candidate["relevance_score"] = relevance
                    candidate["verify_reason"] = item.get("reason", "")
                    verified.append(candidate)

            verified.sort(
                key=lambda x: x.get("maturity_score", 0) * x.get("confidence", 0) * x.get("relevance_score", 0),
                reverse=True,
            )
            return verified[:max_inject]

        except Exception as e:
            logger.warning(f"LLM relevance verification failed: {e}")
            return None

    def _fallback_rank(self, candidates: list[dict]) -> list[dict]:
        max_inject = self._settings.experience_max_inject
        for c in candidates:
            c["relevance_score"] = 1.0
        candidates.sort(
            key=lambda x: x.get("maturity_score", 0) * x.get("confidence", 0),
            reverse=True,
        )
        return candidates[:max_inject]

    @staticmethod
    def _skill_to_dict(skill) -> dict:
        return {
            "id": str(skill.id),
            "cluster_id": skill.cluster_id,
            "group_id": skill.group_id,
            "name": skill.name,
            "description": skill.description,
            "content": skill.content,
            "skill_form": skill.skill_form,
            "confidence": skill.confidence,
            "maturity_score": skill.maturity_score,
        }

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
        return {}
