"""Celery tasks for feedback processing pipeline.

Handles:
- Single feedback processing (skill linkage + confidence update)
- Batch feedback processing
- Skill content rewriting triggered by refine feedback
"""

import logging
from datetime import datetime

from celery import shared_task
from sqlalchemy import select, and_

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def process_feedback(self, feedback_id: str) -> dict:
    """Process a single accepted feedback record.

    Pipeline stages:
    1. Resolve skill linkage (find affected_skill_id)
    2. Apply confidence delta to the linked skill
    3. Trigger downstream actions (retirement check, skill rewrite)

    Returns a summary dict for audit purposes.
    """
    import asyncio
    return asyncio.run(_process_feedback_async(feedback_id))


async def _process_feedback_async(feedback_id: str) -> dict:
    """Async implementation of feedback processing."""
    from backend.models.base import async_session_factory
    from backend.experience.models import ExperienceFeedback

    async with async_session_factory() as db:
        result = await db.execute(
            select(ExperienceFeedback).where(
                ExperienceFeedback.id == feedback_id,
                ExperienceFeedback.status == "accepted",
            )
        )
        feedback = result.scalar_one_or_none()
        if not feedback:
            logger.warning(f"Feedback {feedback_id} not found or not accepted")
            return {"status": "skipped", "reason": "not_found"}

        summary = {
            "feedback_id": feedback_id,
            "feedback_type": feedback.feedback_type,
            "confidence_delta": feedback.confidence_delta,
            "skill_resolved": False,
            "downstream_action": "none",
        }

        # --- Stage 2: Resolve skill linkage ---
        skill_id = await _resolve_skill_linkage(db, feedback)
        if skill_id:
            feedback.affected_skill_id = skill_id
            summary["skill_resolved"] = True
            summary["skill_id"] = skill_id

            # --- Stage 4: Apply confidence delta ---
            await _apply_confidence_delta(db, skill_id, feedback.confidence_delta)

            # --- Stage 5: Downstream triggers ---
            action = await _trigger_downstream(db, skill_id, feedback)
            summary["downstream_action"] = action
        else:
            logger.info(
                f"No matching skill found for feedback {feedback_id}, "
                f"rule_doc={feedback.rule_doc_name}. "
                f"Feedback stored but has no immediate effect."
            )

        feedback.updated_at = datetime.utcnow()
        await db.commit()

    return summary


async def _resolve_skill_linkage(db, feedback) -> str | None:
    from backend.experience.models import ExperienceSkill

    group_id = feedback.rule_doc_name.rsplit(".", 1)[0] if feedback.rule_doc_name else None
    if not group_id:
        return None

    result = await db.execute(
        select(ExperienceSkill).where(
            ExperienceSkill.group_id == group_id,
            ExperienceSkill.retired_at.is_(None),
        ).order_by(
            ExperienceSkill.confidence.desc()
        ).limit(1)
    )
    skill = result.scalar_one_or_none()
    return str(skill.id) if skill else None


async def _apply_confidence_delta(db, skill_id: str, delta: float) -> None:
    from backend.experience.models import ExperienceSkill
    from backend.config import get_settings

    result = await db.execute(
        select(ExperienceSkill).where(ExperienceSkill.id == skill_id)
    )
    skill = result.scalar_one_or_none()
    if not skill:
        return

    old_confidence = skill.confidence
    skill.confidence = max(0.0, min(0.95, skill.confidence + delta))
    skill.updated_at = datetime.utcnow()

    if skill.confidence < get_settings().experience_confidence_retire:
        skill.retired_at = datetime.utcnow()
        logger.info(
            f"Skill {skill_id} retired: confidence {old_confidence:.2f} "
            f"→ {skill.confidence:.2f} (below retire threshold)"
        )


async def _trigger_downstream(db, skill_id: str, feedback) -> str:
    """Trigger downstream actions based on feedback type.

    Returns the action taken for audit.
    """
    if feedback.feedback_type == "confirm":
        return "confidence_bumped"

    if feedback.feedback_type == "contradict":
        return "confidence_reduced"

    if feedback.feedback_type == "refine":
        # Trigger skill rewrite
        rewrite_skill_from_feedback.delay(feedback.id)
        return "skill_rewrite_triggered"

    return "none"


@shared_task(bind=True, max_retries=1, default_retry_delay=60)
def process_batch_feedback(self, batch_id: str) -> dict:
    """Process all feedback records in a batch.

    Iterates through all feedback records with the given batch_id
    and runs the processing pipeline for each.
    """
    import asyncio
    return asyncio.run(_process_batch_async(batch_id))


async def _process_batch_async(batch_id: str) -> dict:
    """Async implementation of batch feedback processing."""
    from backend.models.base import async_session_factory
    from backend.experience.models import ExperienceFeedback

    processed = 0
    skipped = 0

    async with async_session_factory() as db:
        result = await db.execute(
            select(ExperienceFeedback).where(
                ExperienceFeedback.batch_id == batch_id,
                ExperienceFeedback.status == "accepted",
            )
        )
        feedbacks = result.scalars().all()

        for feedback in feedbacks:
            skill_id = await _resolve_skill_linkage(db, feedback)
            if skill_id:
                feedback.affected_skill_id = skill_id
                await _apply_confidence_delta(db, skill_id, feedback.confidence_delta)
                processed += 1
            else:
                skipped += 1

            feedback.updated_at = datetime.utcnow()

        await db.commit()

    logger.info(
        f"Batch {batch_id}: processed={processed}, skipped={skipped}"
    )
    return {"batch_id": batch_id, "processed": processed, "skipped": skipped}


@shared_task(bind=True, max_retries=1, default_retry_delay=30)
def rewrite_skill_from_feedback(self, feedback_id: str) -> dict:
    """Rewrite a skill's content based on user refine feedback.

    Uses the LLM to incorporate the user's correction into the skill SOP.
    Triggered when a refine feedback is accepted.

    NOTE: This task will be fully implemented in Phase 2 when
    experience_skills table and SkillExtractor are available.
    """
    import asyncio
    return asyncio.run(_rewrite_skill_async(feedback_id))


async def _rewrite_skill_async(feedback_id: str) -> dict:
    from backend.models.base import async_session_factory
    from backend.experience.models import ExperienceFeedback, ExperienceSkill
    from backend.experience.maturity_scorer import MaturityScorer
    from backend.services.llm_factory import create_llm_client
    from mini_agent.schema import Message

    async with async_session_factory() as db:
        result = await db.execute(
            select(ExperienceFeedback).where(
                ExperienceFeedback.id == feedback_id,
                ExperienceFeedback.feedback_type == "refine",
                ExperienceFeedback.status == "accepted",
            )
        )
        feedback = result.scalar_one_or_none()
        if not feedback:
            return {"status": "skipped", "reason": "not_found"}

        if not feedback.affected_skill_id:
            logger.info(
                f"Feedback {feedback_id} has no linked skill, skipping rewrite"
            )
            return {"status": "skipped", "reason": "no_skill"}

        skill_result = await db.execute(
            select(ExperienceSkill).where(
                ExperienceSkill.id == feedback.affected_skill_id
            )
        )
        skill = skill_result.scalar_one_or_none()
        if not skill:
            return {"status": "skipped", "reason": "skill_not_found"}

        old_content = skill.content

        rewrite_prompt = f"""请根据用户修正意见重写以下审查经验技能的内容。

原始技能内容：
{skill.content}

用户修正：
- 修正后严重度：{feedback.corrected_severity or '未修改'}
- 修正后建议：{feedback.corrected_suggestion or '未修改'}
- 修正后合规判定：{feedback.corrected_is_compliant or '未修改'}
- 用户备注：{feedback.comment or '无'}

要求：
1. 保留原有的 ## Steps / ## Pitfalls 结构
2. 将用户修正融入相应步骤
3. 如果修正涉及新的判断逻辑，添加为新的决策分支
4. 仅输出重写后的完整内容，不要输出其他内容"""

        try:
            llm_client = create_llm_client()
            messages = [
                Message(role="system", content="你是审查经验技能重写器。"),
                Message(role="user", content=rewrite_prompt),
            ]
            response = await llm_client.generate(messages=messages)
            new_content = response.content.strip()

            if new_content:
                skill.content = new_content
                skill.updated_at = datetime.utcnow()

                scorer = MaturityScorer()
                if scorer.should_rescore(
                    old_content=old_content,
                    new_content=new_content,
                    is_promotion=False,
                    current_maturity=skill.maturity_score,
                    confidence_delta=-0.1,
                ):
                    score_result = await scorer.score(skill)
                    skill.maturity_score = score_result["maturity_score"]
                    skill.maturity_detail = score_result["maturity_detail"]

                await db.commit()

                return {
                    "status": "success",
                    "feedback_id": feedback_id,
                    "skill_id": str(skill.id),
                    "content_length": len(new_content),
                }
        except Exception as e:
            logger.exception(f"Skill rewrite failed for feedback {feedback_id}: {e}")
            return {"status": "failed", "error": str(e)}

        return {"status": "skipped", "reason": "empty_content"}
