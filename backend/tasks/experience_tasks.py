"""Celery tasks for experience extraction pipeline."""

import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def extract_experience(self, task_id: str) -> dict:
    import asyncio
    return asyncio.run(_extract_experience_async(task_id))


async def _extract_experience_async(task_id: str) -> dict:
    from backend.experience.service import ExperienceService
    service = ExperienceService()
    try:
        result = await service.extract_and_persist(task_id)
        return result
    except Exception as e:
        logger.exception(f"Experience extraction failed for task {task_id}: {e}")
        return {"status": "failed", "task_id": task_id, "error": str(e)}


@shared_task(bind=True, max_retries=1, default_retry_delay=60)
def process_skill_extraction(self, skill_id: str) -> dict:
    import asyncio
    return asyncio.run(_process_skill_extraction_async(skill_id))


async def _process_skill_extraction_async(skill_id: str) -> dict:
    from backend.experience.maturity_scorer import MaturityScorer
    from backend.models.base import async_session_factory
    from backend.experience.models import ExperienceSkill
    from sqlalchemy import select

    async with async_session_factory() as db:
        result = await db.execute(
            select(ExperienceSkill).where(ExperienceSkill.id == skill_id)
        )
        skill = result.scalar_one_or_none()
        if not skill:
            return {"status": "skipped", "reason": "skill_not_found"}

        scorer = MaturityScorer()
        score_result = await scorer.score(skill)
        skill.maturity_score = score_result["maturity_score"]
        skill.maturity_detail = score_result["maturity_detail"]
        skill.updated_at = __import__("datetime").datetime.utcnow()
        await db.commit()

        return {"status": "success", "skill_id": skill_id, **score_result}
