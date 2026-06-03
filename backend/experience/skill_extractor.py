"""SkillExtractor: extract or update ExperienceSkill from Case clusters."""

import json
import logging
import re
from datetime import datetime

from mini_agent.schema import Message

from backend.config import get_settings
from backend.services.llm_factory import create_llm_client
from backend.experience.prompts.skill_success_extract_zh import SKILL_SUCCESS_EXTRACT_PROMPT
from backend.experience.prompts.skill_failure_extract_zh import SKILL_FAILURE_EXTRACT_PROMPT

logger = logging.getLogger(__name__)


class SkillExtractor:
    def __init__(self, llm_client=None):
        self._llm_client = llm_client or create_llm_client()
        self._settings = get_settings()

    async def extract_or_update(
        self,
        case,
        cluster_id: str,
        group_id: str,
        existing_skill,
        db,
    ) -> dict:
        quality_threshold = self._settings.experience_quality_threshold

        if case.quality_score >= quality_threshold:
            return await self._extract_success(case, cluster_id, group_id, existing_skill, db)
        else:
            return await self._extract_failure(case, cluster_id, group_id, existing_skill, db)

    async def _extract_success(self, case, cluster_id: str, group_id: str, existing_skill, db) -> dict:
        prompt = SKILL_SUCCESS_EXTRACT_PROMPT.format(
            task_intent=case.task_intent,
            approach=case.approach,
            key_insight=case.key_insight or "无",
            quality_score=case.quality_score,
            finding_count=case.finding_count,
            existing_skill_content=existing_skill.content if existing_skill else "无",
        )

        try:
            messages = [
                Message(role="system", content="你是审查经验技能提取器。仅输出 JSON。"),
                Message(role="user", content=prompt),
            ]
            response = await self._llm_client.generate(messages=messages)
            extracted = self._parse_json(response.content)
        except Exception as e:
            logger.warning(f"Success skill extraction failed: {e}")
            return {"action": "none", "skill_id": None, "confidence_delta": 0.0}

        if not extracted:
            return {"action": "none", "skill_id": None, "confidence_delta": 0.0}

        return await self._apply_success_result(extracted, case, cluster_id, group_id, existing_skill, db)

    async def _extract_failure(self, case, cluster_id: str, group_id: str, existing_skill, db) -> dict:
        prompt = SKILL_FAILURE_EXTRACT_PROMPT.format(
            task_intent=case.task_intent,
            approach=case.approach,
            key_insight=case.key_insight or "无",
            quality_score=case.quality_score,
            finding_count=case.finding_count,
            existing_skill_content=existing_skill.content if existing_skill else "无",
        )

        try:
            messages = [
                Message(role="system", content="你是审查经验技能提取器（失败案例）。仅输出 JSON。"),
                Message(role="user", content=prompt),
            ]
            response = await self._llm_client.generate(messages=messages)
            extracted = self._parse_json(response.content)
        except Exception as e:
            logger.warning(f"Failure skill extraction failed: {e}")
            return {"action": "none", "skill_id": None, "confidence_delta": 0.0}

        if not extracted:
            return {"action": "none", "skill_id": None, "confidence_delta": 0.0}

        return await self._apply_failure_result(extracted, case, cluster_id, group_id, existing_skill, db)

    async def _apply_success_result(self, extracted: dict, case, cluster_id: str, group_id: str, existing_skill, db) -> dict:
        from backend.experience.models import ExperienceSkill

        overlap_ratio = extracted.get("overlap_ratio", 0.0)
        action = extracted.get("action", "add")
        is_promotion = False

        if existing_skill:
            if "## Potential Steps" in existing_skill.content and "## Steps" in extracted.get("content", ""):
                is_promotion = True
                action = "update"
                confidence = 0.6
                skill_form = "verified"
            elif overlap_ratio >= 0.6:
                action = "update"
                if extracted.get("has_new_branch"):
                    confidence = min(0.95, existing_skill.confidence + 0.1)
                elif extracted.get("is_pure_confirmation"):
                    confidence = min(0.95, existing_skill.confidence + 0.05)
                elif extracted.get("has_contradiction"):
                    confidence = max(0.0, existing_skill.confidence - 0.2)
                else:
                    confidence = min(0.95, existing_skill.confidence + 0.05)
                skill_form = existing_skill.skill_form
            else:
                action = "add"
                confidence = 0.5
                skill_form = "verified"
        else:
            action = "add"
            confidence = 0.5
            skill_form = "verified"

        if action == "update" and existing_skill:
            existing_skill.name = extracted.get("name", existing_skill.name)
            existing_skill.description = extracted.get("description", existing_skill.description)
            existing_skill.content = extracted.get("content", existing_skill.content)
            existing_skill.confidence = confidence
            existing_skill.skill_form = skill_form
            source_ids = existing_skill.source_case_ids or []
            if str(case.id) not in source_ids:
                source_ids.append(str(case.id))
            existing_skill.source_case_ids = source_ids[-9:]
            if is_promotion:
                existing_skill.last_promoted_at = datetime.utcnow()
            existing_skill.updated_at = datetime.utcnow()
            await db.flush()

            return {
                "action": "update",
                "skill_id": str(existing_skill.id),
                "confidence_delta": confidence - (existing_skill.confidence if not is_promotion else 0.6),
                "is_promotion": is_promotion,
            }

        new_skill = ExperienceSkill(
            cluster_id=cluster_id,
            group_id=group_id,
            name=extracted.get("name", "未命名技能"),
            description=extracted.get("description", ""),
            content=extracted.get("content", ""),
            skill_form=skill_form,
            confidence=confidence,
            source_case_ids=[str(case.id)],
        )
        db.add(new_skill)
        await db.flush()

        return {
            "action": "add",
            "skill_id": str(new_skill.id),
            "confidence_delta": 0.0,
            "is_promotion": False,
        }

    async def _apply_failure_result(self, extracted: dict, case, cluster_id: str, group_id: str, existing_skill, db) -> dict:
        from backend.experience.models import ExperienceSkill

        if existing_skill:
            if existing_skill.skill_form == "verified":
                existing_skill.content += "\n\n" + extracted.get("content", "")
                source_ids = existing_skill.source_case_ids or []
                if str(case.id) not in source_ids:
                    source_ids.append(str(case.id))
                existing_skill.source_case_ids = source_ids[-9:]
                existing_skill.updated_at = datetime.utcnow()
                await db.flush()
                return {
                    "action": "update",
                    "skill_id": str(existing_skill.id),
                    "confidence_delta": 0.0,
                    "is_promotion": False,
                }
            else:
                existing_skill.content += "\n\n" + extracted.get("content", "")
                existing_skill.confidence = min(0.95, existing_skill.confidence + 0.05)
                source_ids = existing_skill.source_case_ids or []
                if str(case.id) not in source_ids:
                    source_ids.append(str(case.id))
                existing_skill.source_case_ids = source_ids[-9:]
                existing_skill.updated_at = datetime.utcnow()
                await db.flush()
                return {
                    "action": "update",
                    "skill_id": str(existing_skill.id),
                    "confidence_delta": 0.05,
                    "is_promotion": False,
                }

        new_skill = ExperienceSkill(
            cluster_id=cluster_id,
            group_id=group_id,
            name=extracted.get("name", "未命名技能（假设）"),
            description=extracted.get("description", ""),
            content=extracted.get("content", ""),
            skill_form="hypothesis",
            confidence=0.5,
            source_case_ids=[str(case.id)],
        )
        db.add(new_skill)
        await db.flush()

        return {
            "action": "add",
            "skill_id": str(new_skill.id),
            "confidence_delta": 0.0,
            "is_promotion": False,
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
