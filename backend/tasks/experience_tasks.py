"""Celery tasks for experience extraction pipeline."""

import asyncio
import logging
from datetime import datetime, timezone

from backend.celery_app import celery_app
from backend.utils.mini_agent_utils import setup_mini_agent_path
from sqlalchemy import select

# Ensure Mini-Agent submodule is on sys.path before any experience module
# imports (case_extractor, skill_extractor, etc. all use `from mini_agent.schema import Message`).
setup_mini_agent_path()

logger = logging.getLogger(__name__)


async def _run_with_session(coro_factory):
    """Run an async function with a fresh engine/session, disposing afterwards.

    Celery prefork workers call asyncio.run() which creates a new event loop
    per invocation.  The module-level async_session_factory from base.py has a
    connection pool bound to the *original* loop — reusing it causes
    "Event loop is closed" errors.  Instead we create a task-scoped engine
    and dispose it when done.
    """
    from backend.tasks.review_tasks import create_session_factory

    session_factory, engine = create_session_factory()
    try:
        return await coro_factory(session_factory)
    finally:
        await engine.dispose()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60)
def extract_experience(self, task_id: str) -> dict:
    return asyncio.run(_run_with_session(
        lambda sf: _extract_experience_async(sf, task_id)
    ))


async def _extract_experience_async(session_factory, task_id: str) -> dict:
    from backend.experience.service import ExperienceService
    service = ExperienceService()
    try:
        # ExperienceService internally needs a session factory — inject it
        result = await service.extract_and_persist(task_id, session_factory)
        return result
    except Exception as e:
        logger.exception(f"Experience extraction failed for task {task_id}: {e}")
        return {"status": "failed", "task_id": task_id, "error": str(e)}


@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def process_skill_extraction(self, skill_id: str) -> dict:
    return asyncio.run(_run_with_session(
        lambda sf: _process_skill_extraction_async(sf, skill_id)
    ))


async def _process_skill_extraction_async(session_factory, skill_id: str) -> dict:
    from backend.experience.maturity_scorer import MaturityScorer
    from backend.experience.models import ExperienceSkill

    async with session_factory() as db:
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
        skill.updated_at = datetime.now(timezone.utc)
        await db.commit()

        return {"status": "success", "skill_id": skill_id, **score_result}
