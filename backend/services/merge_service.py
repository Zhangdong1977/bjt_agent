"""Merge service for combining historical review results."""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Project, ReviewTask, ReviewResult, ProjectReviewResult

logger = logging.getLogger(__name__)


class MergeService:
    """Service for merging historical review results with LLM deduplication."""

    def __init__(self, db: AsyncSession, agent=None):
        """Initialize the merge service.

        Args:
            db: Database session
            agent: BidReviewAgent instance for LLM decisions (optional for backwards compatibility)
        """
        self.db = db
        self.agent = agent

    async def _get_llm_merge_decision(
        self,
        new_finding: dict,
        existing_findings: list[dict],
    ) -> dict:
        """调用 LLM 获取合并决策。

        Args:
            new_finding: 新发现
            existing_findings: 现有发现列表

        Returns:
            解析后的决策字典
        """
        from backend.services.merge_decision_parser import parse_merge_decision

        if not self.agent:
            # Fallback if no agent provided
            logger.warning("No agent provided for LLM merge decision, using keep_both")
            return {
                "action": "keep_both",
                "reason": "No agent available",
                "replace_key": None,
                "parse_failed": True,
            }

        try:
            decision_text = await self.agent.decide_merge(new_finding, existing_findings)
            return parse_merge_decision(decision_text)
        except Exception as e:
            logger.warning(f"LLM merge decision failed: {e}, using keep_both strategy")
            return {
                "action": "keep_both",
                "reason": f"LLM调用失败: {str(e)}",
                "replace_key": None,
                "parse_failed": True,
            }

    async def merge_project_results(
        self,
        project_id: str,
        latest_task_id: str,
        event_callback=None,
    ) -> tuple[int, int]:
        """Merge all historical review results for a project.

        Args:
            project_id: Project ID
            latest_task_id: The most recently completed task ID
            event_callback: Optional callback for SSE events

        Returns:
            Tuple of (merged_count, total_count)
        """
        # Send merging event
        if event_callback:
            event_callback("merging", {"message": "正在合并历史结果..."})

        # Get all historical ReviewResult for this project
        historical_results = await self._get_historical_results(project_id)
        logger.info(f"[merge] Found {len(historical_results)} historical ReviewResult records")

        # Get existing ProjectReviewResult records
        existing_merged = await self._get_existing_merged(project_id)
        logger.info(f"[merge] Found {len(existing_merged)} existing ProjectReviewResult records")

        # Build map of existing by requirement_key
        existing_by_key: dict[str, dict] = {}
        for rec in existing_merged:
            key = rec.get("requirement_key", "")
            if key:
                existing_by_key[key] = rec

        # Track which existing records were matched
        matched_keys = set()

        # Process new results from latest task
        latest_results = [r for r in historical_results if r["task_id"] == latest_task_id]
        new_merged_records: list[dict] = []
        merge_count = 0

        for new_result in latest_results:
            req_key = new_result.get("requirement_key", "")

            if req_key in existing_by_key:
                existing = existing_by_key[req_key]

                # 使用 LLM 决策
                decision = await self._get_llm_merge_decision(
                    new_result,
                    [existing]
                )

                if decision["action"] == "keep":
                    merged_record = {**new_result, "merged_from_count": 2}
                    new_merged_records.append(merged_record)
                    matched_keys.add(req_key)
                    merge_count += 1
                elif decision["action"] == "replace":
                    merged_record = {**existing, **new_result}
                    merged_record["merged_from_count"] = existing.get("merged_from_count", 1) + 1
                    new_merged_records.append(merged_record)
                    matched_keys.add(req_key)
                    merge_count += 1
                elif decision["action"] == "discard":
                    new_merged_records.append(existing)
                    matched_keys.add(req_key)
                elif decision["action"] == "keep_both":
                    new_record_copy = {**new_result, "merged_from_count": 1}
                    existing_record_copy = {**existing, "merged_from_count": existing.get("merged_from_count", 1)}
                    new_merged_records.append(existing_record_copy)
                    new_merged_records.append(new_record_copy)
                    matched_keys.add(req_key)
            else:
                # 新 key，直接添加
                merged_record = {**new_result, "merged_from_count": 1}
                new_merged_records.append(merged_record)
                matched_keys.add(req_key)

        # Handle historical records not in latest task
        for rec in existing_merged:
            req_key = rec.get("requirement_key", "")
            if req_key not in matched_keys:
                new_merged_records.append(rec)

        # Delete all existing ProjectReviewResult for this project
        await self.db.execute(
            delete(ProjectReviewResult).where(ProjectReviewResult.project_id == project_id)
        )

        # Insert merged records
        now = datetime.utcnow()
        for record in new_merged_records:
            prr = ProjectReviewResult(
                id=record.get("id") or str(uuid.uuid4()),
                project_id=project_id,
                requirement_key=record["requirement_key"],
                requirement_content=record["requirement_content"],
                bid_content=record.get("bid_content"),
                is_compliant=record.get("is_compliant", False),
                severity=record["severity"],
                location_page=record.get("location_page"),
                location_line=record.get("location_line"),
                suggestion=record.get("suggestion"),
                explanation=record.get("explanation"),
                source_task_id=record["task_id"],
                merged_from_count=record.get("merged_from_count", 1),
                created_at=now,
                updated_at=now,
            )
            self.db.add(prr)

        await self.db.commit()

        # Send merged event
        if event_callback:
            event_callback("merged", {
                "merged_count": merge_count,
                "total_count": len(new_merged_records),
            })

        logger.info(f"[merge] Completed: {merge_count} merged, {len(new_merged_records)} total")
        return merge_count, len(new_merged_records)

    async def _get_historical_results(self, project_id: str) -> list[dict]:
        """Get all ReviewResult records for a project's tasks."""
        result = await self.db.execute(
            select(ReviewResult)
            .join(ReviewTask, ReviewTask.id == ReviewResult.task_id)
            .where(ReviewTask.project_id == project_id)
        )
        records = result.scalars().all()
        return [
            {
                "id": str(r.id),
                "task_id": str(r.task_id),
                "requirement_key": r.requirement_key,
                "requirement_content": r.requirement_content,
                "bid_content": r.bid_content,
                "is_compliant": r.is_compliant,
                "severity": r.severity,
                "location_page": r.location_page,
                "location_line": r.location_line,
                "suggestion": r.suggestion,
                "explanation": r.explanation,
            }
            for r in records
        ]

    async def _get_existing_merged(self, project_id: str) -> list[dict]:
        """Get existing ProjectReviewResult records for a project."""
        result = await self.db.execute(
            select(ProjectReviewResult).where(ProjectReviewResult.project_id == project_id)
        )
        records = result.scalars().all()
        return [
            {
                "id": str(r.id),
                "requirement_key": r.requirement_key,
                "requirement_content": r.requirement_content,
                "bid_content": r.bid_content,
                "is_compliant": r.is_compliant,
                "severity": r.severity,
                "location_page": r.location_page,
                "location_line": r.location_line,
                "suggestion": r.suggestion,
                "explanation": r.explanation,
                "task_id": str(r.source_task_id),
                "merged_from_count": r.merged_from_count,
            }
            for r in records
        ]
