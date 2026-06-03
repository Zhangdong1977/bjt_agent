"""ExperienceService: entry coordinator for the experience extraction pipeline."""

import logging

from backend.experience.case_extractor import CaseExtractor
from backend.experience.cluster_manager import ClusterManager
from backend.experience.skill_extractor import SkillExtractor
from backend.experience.maturity_scorer import MaturityScorer

logger = logging.getLogger(__name__)


class ExperienceService:
    def __init__(self):
        self.case_extractor = CaseExtractor()
        self.cluster_manager = ClusterManager()
        self.skill_extractor = SkillExtractor()
        self.maturity_scorer = MaturityScorer()

    async def extract_and_persist(self, task_id: str) -> dict:
        summary = {
            "task_id": task_id,
            "case_extracted": False,
            "cluster_assigned": False,
            "skill_action": "none",
            "maturity_scored": False,
        }

        db, task_info, agent_steps, findings = await self._load_review_data(task_id)
        if not db:
            return summary

        try:
            case_data = await self._extract_case(task_id, task_info, agent_steps, findings, db)
            if not case_data:
                return summary
            summary["case_extracted"] = True

            case = await self._persist_case(case_data, db)

            cluster_id = await self._assign_cluster(case, db)
            if cluster_id:
                summary["cluster_assigned"] = True

            existing_skill = await self._load_existing_skill(cluster_id, case.group_id, db)

            skill_result = await self._extract_skill(case, cluster_id, case.group_id, existing_skill, db)
            summary["skill_action"] = skill_result.get("action", "none")
            summary["skill_id"] = skill_result.get("skill_id")

            if skill_result.get("skill_id"):
                scored = await self._score_maturity(skill_result, existing_skill, db)
                summary["maturity_scored"] = scored

            await db.commit()
            return summary

        except Exception as e:
            logger.exception(f"Experience pipeline failed for task {task_id}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            return summary
        finally:
            try:
                await db.close()
            except Exception:
                pass

    async def _load_review_data(self, task_id: str):
        from backend.models.base import async_session_factory
        from backend.models import ReviewTask, AgentStep, ReviewResult
        from sqlalchemy import select

        try:
            db = async_session_factory()
            task_result = await db.execute(
                select(ReviewTask).where(ReviewTask.id == task_id)
            )
            review_task = task_result.scalar_one_or_none()
            if not review_task:
                logger.warning(f"ReviewTask {task_id} not found")
                await db.close()
                return None, None, None, None

            steps_result = await db.execute(
                select(AgentStep).where(AgentStep.task_id == task_id).order_by(AgentStep.step_number)
            )
            agent_steps = steps_result.scalars().all()

            findings_result = await db.execute(
                select(ReviewResult).where(ReviewResult.task_id == task_id)
            )
            findings = findings_result.scalars().all()

            task_info = {
                "task_id": task_id,
                "project_id": str(review_task.project_id),
                "user_id": str(review_task.user_id),
                "rule_doc_name": getattr(review_task, "rule_doc_name", None),
            }

            return db, task_info, agent_steps, findings
        except Exception as e:
            logger.exception(f"Failed to load review data for task {task_id}: {e}")
            return None, None, None, None

    async def _extract_case(self, task_id: str, task_info: dict, agent_steps, findings, db) -> dict | None:
        try:
            rule_doc_name = task_info.get("rule_doc_name") or ""
            group_id = rule_doc_name.rsplit(".", 1)[0] if rule_doc_name else "unknown"

            findings_dicts = []
            for f in findings:
                findings_dicts.append({
                    "id": str(f.id),
                    "requirement_key": getattr(f, "requirement_key", None),
                    "is_compliant": getattr(f, "is_compliant", None),
                    "severity": getattr(f, "severity", None),
                    "explanation": getattr(f, "explanation", None),
                    "suggestion": getattr(f, "suggestion", None),
                    "bid_content": getattr(f, "bid_content", None),
                    "requirement_content": getattr(f, "requirement_content", None),
                })

            return await self.case_extractor.extract(
                task_id=task_id,
                project_id=task_info["project_id"],
                rule_doc_name=rule_doc_name,
                group_id=group_id,
                user_id=task_info["user_id"],
                agent_steps=agent_steps,
                findings=findings_dicts,
            )
        except Exception as e:
            logger.warning(f"Case extraction failed for task {task_id}: {e}")
            return None

    async def _persist_case(self, case_data: dict, db):
        from backend.experience.models import ExperienceCase

        case = ExperienceCase(**case_data)
        db.add(case)
        await db.flush()
        return case

    async def _assign_cluster(self, case, db) -> str | None:
        try:
            return await self.cluster_manager.assign_cluster(case, db)
        except Exception as e:
            logger.warning(f"Cluster assignment failed for case {case.id}: {e}")
            return None

    async def _load_existing_skill(self, cluster_id: str | None, group_id: str, db):
        from backend.experience.models import ExperienceSkill
        from sqlalchemy import select

        if not cluster_id:
            return None

        try:
            result = await db.execute(
                select(ExperienceSkill).where(
                    ExperienceSkill.cluster_id == cluster_id,
                    ExperienceSkill.group_id == group_id,
                    ExperienceSkill.retired_at.is_(None),
                ).order_by(
                    ExperienceSkill.confidence.desc()
                ).limit(1)
            )
            return result.scalar_one_or_none()
        except Exception as e:
            logger.warning(f"Failed to load existing skill for cluster {cluster_id}: {e}")
            return None

    async def _extract_skill(self, case, cluster_id: str, group_id: str, existing_skill, db) -> dict:
        try:
            return await self.skill_extractor.extract_or_update(
                case=case,
                cluster_id=cluster_id,
                group_id=group_id,
                existing_skill=existing_skill,
                db=db,
            )
        except Exception as e:
            logger.warning(f"Skill extraction failed for case {case.id}: {e}")
            return {"action": "none", "skill_id": None}

    async def _score_maturity(self, skill_result: dict, existing_skill, db) -> bool:
        from backend.experience.models import ExperienceSkill
        from sqlalchemy import select

        skill_id = skill_result.get("skill_id")
        if not skill_id:
            return False

        try:
            result = await db.execute(
                select(ExperienceSkill).where(ExperienceSkill.id == skill_id)
            )
            skill = result.scalar_one_or_none()
            if not skill:
                return False

            old_content = existing_skill.content if existing_skill else ""
            new_content = skill.content
            is_promotion = skill_result.get("is_promotion", False)

            if self.maturity_scorer.should_rescore(
                old_content=old_content,
                new_content=new_content,
                is_promotion=is_promotion,
                current_maturity=skill.maturity_score,
                confidence_delta=skill_result.get("confidence_delta", 0.0),
            ):
                score_result = await self.maturity_scorer.score(skill)
                skill.maturity_score = score_result["maturity_score"]
                skill.maturity_detail = score_result["maturity_detail"]
                return True

            return False
        except Exception as e:
            logger.warning(f"Maturity scoring failed for skill {skill_id}: {e}")
            return False
