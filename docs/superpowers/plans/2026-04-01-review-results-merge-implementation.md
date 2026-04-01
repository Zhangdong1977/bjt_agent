# Review Results Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement AI-powered semantic deduplication of review results across historical tasks, storing merged results in a new ProjectReviewResult table.

**Architecture:** After each review task completes, a Celery task queries all historical ReviewResult records for the project, uses Mini-Max embedding API to compute semantic similarity, merges duplicates by keeping the higher-severity record, and persists to a new project_review_results table. Frontend receives SSE events to show merge progress.

**Tech Stack:** Python (FastAPI/Celery), SQLAlchemy async, Mini-Max API (OpenAI-compatible embeddings), PostgreSQL, Vue3/Pinia

---

## File Structure

### New Files
- `backend/models/project_review_result.py` — ProjectReviewResult model
- `backend/services/embedding_service.py` — Mini-Max embedding API wrapper
- `backend/services/merge_service.py` — AI deduplication merge logic

### Modified Files
- `backend/models/__init__.py` — Export ProjectReviewResult
- `backend/schemas/review.py` — Add ProjectReviewResultResponse schema
- `backend/api/review.py` — Change get_review_results to query ProjectReviewResult
- `backend/tasks/review_tasks.py` — Add merge_review_results task, trigger after run_review
- `frontend/src/stores/project.ts` — Handle SSE merging/merged events

---

## Task 1: Create ProjectReviewResult Model

**Files:**
- Create: `backend/models/project_review_result.py`
- Modify: `backend/models/__init__.py:7`

- [ ] **Step 1: Create project_review_result.py**

```python
"""Project-level merged review result model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Integer, ForeignKey, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .project import Project


class ProjectReviewResult(Base):
    """Project-level merged review result.

    This table stores the deduplicated, merged review results across all
    historical review tasks for a project.
    """

    __tablename__ = "project_review_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requirement_content: Mapped[str] = mapped_column(Text, nullable=False)
    bid_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_compliant: Mapped[bool] = mapped_column(Boolean, default=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)  # critical, major, minor
    location_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_task_id: Mapped[str] = mapped_column(String(36), ForeignKey("review_tasks.id"), nullable=False)
    merged_from_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="project_review_results")

    def __repr__(self) -> str:
        return f"<ProjectReviewResult(id={self.id}, severity={self.severity}, merged_from={self.merged_from_count})>"
```

- [ ] **Step 2: Update backend/models/__init__.py**

Add import and export:
```python
from .project_review_result import ProjectReviewResult

__all__ = [
    ...
    "ProjectReviewResult",
]
```

- [ ] **Step 3: Add relationship to Project model**

Modify `backend/models/project.py` to add:
```python
project_review_results: Mapped[list["ProjectReviewResult"]] = relationship(
    "ProjectReviewResult", back_populates="project", cascade="all, delete-orphan"
)
```

- [ ] **Step 4: Commit**

```bash
git add backend/models/project_review_result.py backend/models/__init__.py backend/models/project.py
git commit -m "feat(models): add ProjectReviewResult for merged project-level findings"
```

---

## Task 2: Create Embedding Service

**Files:**
- Create: `backend/services/embedding_service.py`

- [ ] **Step 1: Create embedding_service.py**

```python
"""Embedding service for semantic similarity using Mini-Max API."""

import logging
from typing import Literal

from openai import AsyncOpenAI

from backend.config import get_settings

logger = logging.getLogger(__name__)

# Severity ordering for merge priority
SEVERITY_ORDER: dict[str, int] = {
    "critical": 3,
    "major": 2,
    "minor": 1,
}


class EmbeddingService:
    """Service for computing text embeddings via Mini-Max API."""

    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(
            api_key=settings.mini_agent_api_key,
            base_url=settings.mini_agent_api_base,
        )
        self.model = "embeddings"  # MiniMax embedding model

    async def get_embedding(self, text: str) -> list[float]:
        """Get embedding vector for a single text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Embedding API error: {e}")
            raise

    async def compute_similarity(self, text1: str, text2: str) -> float:
        """Compute cosine similarity between two texts.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score between 0.0 and 1.0
        """
        import math

        embedding1 = await self.get_embedding(text1)
        embedding2 = await self.get_embedding(text2)

        # Cosine similarity
        dot_product = sum(a * b for a, b in zip(embedding1, embedding2))
        magnitude1 = math.sqrt(sum(a * a for a in embedding1))
        magnitude2 = math.sqrt(sum(b * b for b in embedding2))

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)

    def merge_candidates(
        self,
        existing: dict,
        new: dict,
        similarity_threshold: float = 0.85,
    ) -> tuple[dict | None, bool]:
        """Determine if new result should merge with existing.

        Compares requirement_content + bid_content + explanation + suggestion
        and returns the better record based on severity.

        Args:
            existing: Existing ProjectReviewResult dict
            new: New ReviewResult dict
            similarity_threshold: Minimum similarity to consider as duplicate

        Returns:
            Tuple of (merged_record, is_duplicate)
            - merged_record: The record to store (existing if severity higher, else new)
            - is_duplicate: True if texts are semantically similar
        """
        # Build comparison text
        existing_text = self._build_comparison_text(existing)
        new_text = self._build_comparison_text(new)

        # Compute similarity synchronously (will be called from async context)
        import asyncio
        loop = asyncio.get_event_loop()
        similarity = loop.run_until_complete(self.compute_similarity(existing_text, new_text))

        if similarity >= similarity_threshold:
            # Determine which to keep based on severity
            existing_severity_rank = SEVERITY_ORDER.get(existing.get("severity", "minor"), 0)
            new_severity_rank = SEVERITY_ORDER.get(new.get("severity", "minor"), 0)

            if new_severity_rank >= existing_severity_rank:
                return new, True
            else:
                return existing, True

        return None, False

    def _build_comparison_text(self, record: dict) -> str:
        """Build text for similarity comparison from record fields."""
        parts = []
        for field in ["requirement_content", "bid_content", "explanation", "suggestion"]:
            if record.get(field):
                parts.append(str(record[field]))
        return " ".join(parts)
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/embedding_service.py
git commit -m "feat(service): add EmbeddingService for semantic similarity"
```

---

## Task 3: Create Merge Service

**Files:**
- Create: `backend/services/merge_service.py`

- [ ] **Step 1: Create merge_service.py**

```python
"""Merge service for combining historical review results."""

import logging
import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Project, ReviewTask, ReviewResult, ProjectReviewResult
from backend.services.embedding_service import EmbeddingService

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
        from backend.services.embedding_service import SEVERITY_ORDER

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
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/merge_service.py
git commit -m "feat(service): add MergeService for AI deduplication"
```

---

## Task 4: Add Celery Merge Task

**Files:**
- Modify: `backend/tasks/review_tasks.py:188-215`

- [ ] **Step 1: Add merge_review_results task to review_tasks.py**

Add after line 214 (after `return run_async(_run())` in `run_review`):

```python
@celery_app.task(bind=True, name="backend.tasks.review_tasks.merge_review_results")
def merge_review_results(self, project_id: str, latest_task_id: str) -> dict:
    """Merge historical review results for a project.

    This task:
    1. Queries all historical ReviewResult for the project
    2. Uses AI semantic similarity to deduplicate
    3. Stores merged results in project_review_results table
    4. Publishes SSE events for frontend progress
    """
    def event_cb(event_type: str, data: dict):
        _publish_event(latest_task_id, event_type, data)

    async def _run_merge():
        session_factory = create_session_factory()
        async with session_factory() as db:
            from backend.services.merge_service import MergeService
            merge_service = MergeService(db)
            merged_count, total_count = await merge_service.merge_project_results(
                project_id=project_id,
                latest_task_id=latest_task_id,
                event_callback=event_cb,
            )
            return {"status": "success", "merged_count": merged_count, "total_count": total_count}

    return run_async(_run_merge())
```

- [ ] **Step 2: Trigger merge after run_review completes**

In `run_review` function, after line 191 (`await db.commit()`) and before the completion event, add:

```python
# Trigger merge task after successful completion
from backend.tasks.review_tasks import merge_review_results
merge_review_results.delay(project_id=task.project_id, latest_task_id=task_id)
```

- [ ] **Step 3: Commit**

```bash
git add backend/tasks/review_tasks.py
git commit -m "feat(tasks): add merge_review_results Celery task"
```

---

## Task 5: Update API to Use ProjectReviewResult

**Files:**
- Modify: `backend/api/review.py:99-152`
- Modify: `backend/schemas/review.py`

- [ ] **Step 1: Add ProjectReviewResultResponse schema in review.py**

Add after `ReviewResultResponse` class (after line 21):

```python
class ProjectReviewResultResponse(BaseModel):
    """Schema for merged project-level review result."""

    id: str
    requirement_key: str
    requirement_content: str
    bid_content: str | None
    is_compliant: bool
    severity: str
    location_page: int | None
    location_line: int | None
    suggestion: str | None
    explanation: str | None
    source_task_id: str
    merged_from_count: int

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Update get_review_results to query ProjectReviewResult**

Replace lines 99-152 in `backend/api/review.py`:

```python
@router.get("", response_model=ReviewResponse)
async def get_review_results(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ReviewResponse:
    """Get the merged review results for the project.

    Returns all merged non-compliant findings across all historical review tasks.
    """
    await verify_project_ownership(project_id, current_user.id, db)

    # Get all merged results for this project
    result = await db.execute(
        select(ProjectReviewResult)
        .where(ProjectReviewResult.project_id == project_id)
        .order_by(
            ProjectReviewResult.severity.asc(),  # critical first
            ProjectReviewResult.created_at.asc(),
        )
    )
    findings = result.scalars().all()
    logger.info(f"[get_review_results] project_id={project_id}, findings_count={len(findings)}")

    # Calculate summary
    summary = {
        "total_requirements": len(findings),
        "compliant": sum(1 for f in findings if f.is_compliant),
        "non_compliant": sum(1 for f in findings if not f.is_compliant),
        "critical": sum(1 for f in findings if f.severity == "critical" and not f.is_compliant),
        "major": sum(1 for f in findings if f.severity == "major" and not f.is_compliant),
        "minor": sum(1 for f in findings if f.severity == "minor" and not f.is_compliant),
    }

    return ReviewResponse(summary=summary, findings=findings)
```

- [ ] **Step 3: Add ProjectReviewResult to imports**

In `backend/api/review.py` line 11, update the import:
```python
from backend.models import Project, ReviewTask, ReviewResult, AgentStep, ProjectReviewResult
```

- [ ] **Step 4: Commit**

```bash
git add backend/api/review.py backend/schemas/review.py
git commit -m "feat(api): query ProjectReviewResult for merged findings"
```

---

## Task 6: Update Frontend SSE Handling

**Files:**
- Modify: `frontend/src/stores/project.ts`

- [ ] **Step 1: Add merging/merged case to handleSSEEvent**

Find the `handleSSEEvent` function and add before the `case 'complete':` block:

```typescript
case 'merging':
    console.log('[SSE] 正在合并历史结果...', event.message)
    // Could show a global loading indicator here
    break
case 'merged':
    console.log('[SSE] 合并完成, merged_count:', event.merged_count, 'total_count:', event.total_count)
    fetchReviewResults()
    break
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/stores/project.ts
git commit -m "feat(frontend): handle merging/merged SSE events"
```

---

## Task 7: Create Database Migration

**Files:**
- Create: `backend/migrations/` or use Alembic

- [ ] **Step 1: Create migration SQL**

```sql
-- Migration: Create project_review_results table
-- Run this manually or via Alembic

CREATE TABLE IF NOT EXISTS project_review_results (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    requirement_key VARCHAR(255) NOT NULL,
    requirement_content TEXT NOT NULL,
    bid_content TEXT,
    is_compliant BOOLEAN DEFAULT FALSE,
    severity VARCHAR(50) NOT NULL,
    location_page INTEGER,
    location_line INTEGER,
    suggestion TEXT,
    explanation TEXT,
    source_task_id VARCHAR(36) NOT NULL REFERENCES review_tasks(id),
    merged_from_count INTEGER DEFAULT 1,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_review_results_project_id ON project_review_results(project_id);
```

- [ ] **Step 2: Execute migration**

Run the SQL against the database:
```bash
PGPASSWORD='y6+YufO6njlzxXiaNj6rA4xZaT3ofwT6' psql -h 183.66.37.186 -p 7004 -U ssirs_user -d bjt_agent -c "$(cat migration.sql)"
```

- [ ] **Step 3: Commit migration file**

```bash
git add backend/migrations/001_create_project_review_results.sql
git commit -m "migrate: add project_review_results table"
```

---

## Verification Steps

After all tasks complete:

1. **Start services**: `./scripts/bjt.sh restart`
2. **Login as zhangdong**, navigate to the test project
3. **Start a new review task**
4. **Watch logs**:
   - Backend: `tail -f scripts/logs/celery_review.log | grep merge`
   - Frontend Console: Should see `[SSE] 正在合并历史结果...` then `[SSE] 合并完成`
5. **Verify database**:
   ```sql
   SELECT * FROM project_review_results WHERE project_id = 'e5eb1ee9-0d0e-4e28-b9c7-7f6a762eb8ce';
   ```
6. **Frontend should show merged count**, not just latest task's findings

---

## Self-Review Checklist

- [ ] Spec coverage: All requirements from design spec implemented
- [ ] No placeholders: All TODOs/TBDs resolved
- [ ] Type consistency: ProjectReviewResult fields match ReviewResultResponse schema
- [ ] Import paths correct: All imports use correct relative paths
- [ ] API change covered: Frontend SSE handling added for merging/merged
- [ ] Migration exists: SQL for creating new table committed
