"""Merge service for combining historical review results."""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Project, ReviewTask, ReviewResult, ProjectReviewResult
from backend.services.embedding_service import EmbeddingService, SEVERITY_ORDER

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.85  # 85% similarity threshold for deduplication


class MergeService:
    """Service for merging historical review results with AI deduplication."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.embedding_service = EmbeddingService()

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
                # Check semantic similarity
                existing = existing_by_key[req_key]
                merged_record, is_duplicate = await self._check_and_merge(
                    existing, new_result, SIMILARITY_THRESHOLD
                )

                if is_duplicate:
                    merge_count += 1
                    new_merged_records.append(merged_record)
                    matched_keys.add(req_key)
                else:
                    # Not similar enough, add as new
                    new_result["merged_from_count"] = 1
                    new_merged_records.append(new_result)
                    matched_keys.add(req_key)
            else:
                # New requirement_key, add as new
                new_result["merged_from_count"] = 1
                new_merged_records.append(new_result)
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

    async def _check_and_merge(
        self,
        existing: dict,
        new: dict,
        threshold: float,
    ) -> tuple[dict, bool]:
        """Check similarity and merge two records.

        Returns (record_to_keep, is_duplicate).
        """
        existing_text = self._build_text(existing)
        new_text = self._build_text(new)

        similarity = await self.embedding_service.compute_similarity(existing_text, new_text)

        if similarity >= threshold:
            # Determine which to keep based on severity
            existing_rank = SEVERITY_ORDER.get(existing.get("severity", "minor"), 0)
            new_rank = SEVERITY_ORDER.get(new.get("severity", "minor"), 0)

            if new_rank >= existing_rank:
                # New has higher/equal severity, update existing record
                existing["requirement_content"] = new.get("requirement_content", existing["requirement_content"])
                existing["bid_content"] = new.get("bid_content") or existing.get("bid_content")
                existing["severity"] = new.get("severity", existing["severity"])
                existing["explanation"] = new.get("explanation") or existing.get("explanation")
                existing["suggestion"] = new.get("suggestion") or existing.get("suggestion")
                existing["task_id"] = new.get("task_id", existing["task_id"])
                existing["merged_from_count"] = existing.get("merged_from_count", 1) + 1
                return existing, True
            else:
                # Keep existing, mark that new was merged into it
                existing["merged_from_count"] = existing.get("merged_from_count", 1) + 1
                return existing, True

        return new, False

    def _build_text(self, record: dict) -> str:
        """Build comparison text from record fields."""
        parts = []
        for field in ["requirement_content", "bid_content", "explanation", "suggestion"]:
            if record.get(field):
                parts.append(str(record[field]))
        return " ".join(parts)
