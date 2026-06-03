"""MaturityScorer: four-axis maturity scoring for ExperienceSkills."""

import json
import logging
import re

from mini_agent.schema import Message

from backend.services.llm_factory import create_llm_client
from backend.experience.prompts.skill_maturity_score_zh import SKILL_MATURITY_SCORE_PROMPT

logger = logging.getLogger(__name__)


class MaturityScorer:
    def __init__(self, llm_client=None):
        self._llm_client = llm_client or create_llm_client()

    async def score(self, skill) -> dict:
        prompt = SKILL_MATURITY_SCORE_PROMPT.format(
            skill_name=skill.name,
            skill_description=skill.description,
            skill_content=skill.content[:3000],
        )

        try:
            messages = [
                Message(role="system", content="你是审查经验成熟度评分器。仅输出 JSON。"),
                Message(role="user", content=prompt),
            ]
            response = await self._llm_client.generate(messages=messages)
            result = self._parse_json(response.content)

            completeness = max(1, min(5, result.get("completeness", 3)))
            executability = max(1, min(5, result.get("executability", 3)))
            evidence = max(1, min(5, result.get("evidence", 3)))
            clarity = max(1, min(5, result.get("clarity", 3)))

            maturity_score = (completeness + executability + evidence + clarity) / 20.0

            return {
                "maturity_score": round(maturity_score, 3),
                "maturity_detail": {
                    "completeness": completeness,
                    "executability": executability,
                    "evidence": evidence,
                    "clarity": clarity,
                },
                "reasoning": result.get("reasoning", ""),
            }
        except Exception as e:
            logger.warning(f"Maturity scoring failed for skill {skill.id}: {e}")
            return {
                "maturity_score": 0.0,
                "maturity_detail": None,
                "reasoning": f"Scoring failed: {e}",
            }

    def should_rescore(
        self,
        old_content: str,
        new_content: str,
        is_promotion: bool,
        current_maturity: float,
        confidence_delta: float,
    ) -> bool:
        if is_promotion:
            return True

        if not old_content or not new_content:
            return True

        change_ratio = self._compute_change_ratio(old_content, new_content)

        if change_ratio < 0.2:
            return False
        if change_ratio >= 0.4:
            return True
        if current_maturity < 0.6 or confidence_delta < 0:
            return True
        return False

    @staticmethod
    def _compute_change_ratio(old: str, new: str) -> float:
        if not old:
            return 1.0
        old_set = set(old.split())
        new_set = set(new.split())
        if not old_set:
            return 1.0
        intersection = old_set & new_set
        union = old_set | new_set
        return 1.0 - len(intersection) / len(union) if union else 0.0

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
