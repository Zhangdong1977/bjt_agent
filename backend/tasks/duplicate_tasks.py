"""Celery task for rule-driven technical-bid duplicate checking.

Pair mode keeps the first-stage A/B contract.  Batch mode builds one global
candidate index for three to ten bidder documents and invokes the rule master
once, while the matrix/cluster persistence layer records every occurrence.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.agent.duplicate_master_agent import DuplicateMasterAgent
from backend.celery_app import celery_app
from backend.config import get_settings
from backend.models import Document, Project, ReviewTask, User
from backend.services.document_artifacts import combine_coverage_summaries
from backend.services.duplicate_batch import MultiDocumentCandidateService
from backend.services.duplicate_batch_persistence import (
    finalize_duplicate_task_matrix,
    seed_duplicate_task_index,
)
from backend.services.duplicate_candidates import (
    DocumentDescriptor,
    DuplicateCandidateService,
)
from backend.services.duplicate_hash import (
    find_identical_content_groups,
    find_identical_content_hash,
)
from backend.services.duplicate_runtime import (
    budget,
    decimal_budget,
    feature,
    snapshot_for_task,
    threshold,
)
from backend.services.duplicate_sources import (
    DuplicateSourceIndex,
    SourceDocumentDescriptor,
)
from backend.tasks.review_tasks import (
    _progress_watchdog,
    _publish_event,
    clear_task_cancelled,
    create_session_factory,
    is_task_cancelled,
    run_async,
)
from backend.utils.time_utils import ensure_utc_aware, utc_now, utc_seconds_between

logger = logging.getLogger(__name__)


def _combined_coverage_status(*documents: Document) -> tuple[str, list[str]]:
    """Combine parser coverage without treating missing data as complete."""

    status, warnings = combine_coverage_summaries(
        document.coverage_summary for document in documents
    )
    document_warnings: list[str] = []
    for document in documents:
        summary = document.coverage_summary
        if summary is None:
            document_warnings.append(f"{document.original_filename}: coverage_summary_missing")
            continue
        if not isinstance(summary, dict):
            document_warnings.append(f"{document.original_filename}: coverage_summary_invalid")
            continue
        if document.status != "parsed":
            document_warnings.append(
                f"{document.original_filename}: parse_status_{document.status}"
            )
        for warning in summary.get("warnings", []) or []:
            value = f"{document.original_filename}: {warning}"
            if value not in document_warnings:
                document_warnings.append(value)
    return status, list(dict.fromkeys(document_warnings or warnings))


def _parsed_path(document: Document) -> str | None:
    value = document.parsed_markdown_path or document.parsed_html_path
    return str(value) if value else None


def _document_sort_key(document: Document) -> tuple[int, str, str]:
    ordinal = getattr(document, "duplicate_ordinal", None)
    return (
        int(ordinal) if ordinal is not None else 999,
        str(getattr(document, "created_at", "")),
        document.original_filename,
    )


@dataclass(slots=True)
class _PreparedDuplicateTask:
    mode: str
    all_documents: list[Document]
    bidder_documents: list[Document]
    usable_documents: list[Document]
    source_documents: list[Document]
    coverage_status: str
    coverage_warnings: list[str]

    @property
    def left_document(self) -> Document:
        return self.usable_documents[0]

    @property
    def right_document(self) -> Document:
        return self.usable_documents[1]


def _prepare_duplicate_task(
    task: ReviewTask,
    project: Project,
    documents: list[Document],
) -> _PreparedDuplicateTask:
    mode = str(getattr(task, "duplicate_mode", None) or getattr(project, "duplicate_mode", "pair"))
    if mode not in {"pair", "batch"}:
        raise ValueError("duplicate task mode is invalid")

    if mode == "batch":
        bidders = sorted(
            [document for document in documents if document.doc_type == "duplicate_bid"],
            key=_document_sort_key,
        )
        if not 3 <= len(bidders) <= 10:
            raise ValueError("batch duplicate tasks require 3 to 10 bidder documents")
    else:
        left = [document for document in documents if document.doc_type == "duplicate_left"]
        right = [document for document in documents if document.doc_type == "duplicate_right"]
        if len(left) != 1 or len(right) != 1:
            raise ValueError("pair duplicate tasks require exactly one left and one right document")
        bidders = [left[0], right[0]]

    sources = [
        document
        for document in documents
        if document.doc_type in {"duplicate_tender", "duplicate_public_reference"}
    ]
    usable = [
        document
        for document in bidders
        if document.status == "parsed"
        and _parsed_path(document)
        and Path(_parsed_path(document)).exists()
    ]
    if mode == "pair" and len(usable) != 2:
        raise ValueError("both pair documents must be parsed before duplicate checking")
    if mode == "batch" and len(usable) < 2:
        raise ValueError("at least two parsed batch documents are required")

    coverage_status, coverage_warnings = _combined_coverage_status(
        *bidders, *sources
    )
    for document in bidders:
        if document not in usable:
            coverage_warnings.append(
                f"{document.original_filename}: excluded_from_candidate_index"
            )
    return _PreparedDuplicateTask(
        mode=mode,
        all_documents=documents,
        bidder_documents=bidders,
        usable_documents=usable,
        source_documents=sources,
        coverage_status=coverage_status,
        coverage_warnings=list(dict.fromkeys(coverage_warnings)),
    )


def _descriptor(document: Document) -> DocumentDescriptor:
    path = _parsed_path(document)
    if not path:
        raise ValueError(f"parsed path missing for {document.original_filename}")
    return DocumentDescriptor(
        id=document.id,
        filename=document.original_filename,
        path=path,
        evidence_blocks_path=document.evidence_blocks_path,
        role=document.doc_type,
    )


def _source_descriptor(document: Document) -> SourceDocumentDescriptor:
    return SourceDocumentDescriptor(
        id=document.id,
        filename=document.original_filename,
        evidence_blocks_path=document.evidence_blocks_path,
        source_basis=(
            "tender"
            if document.doc_type == "duplicate_tender"
            else "public"
        ),
        snapshot_hash=document.source_snapshot_hash,
        version=document.source_version,
        source_uri=document.source_uri,
    )


def _hash_guard_items(documents: list[Document]) -> list[tuple[str, str | None, str | None]]:
    return [
        (document.id, document.file_path, _parsed_path(document))
        for document in documents
    ]


async def _cancellation_monitor(session_factory, task_id: str, cancel_event: asyncio.Event):
    """Mirror review-agent cancellation and frontend heartbeat semantics."""

    while not cancel_event.is_set():
        await asyncio.sleep(5)
        if is_task_cancelled(task_id):
            cancel_event.set()
            return
        async with session_factory() as db:
            task = (
                await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
            ).scalar_one_or_none()
            if task is None or task.status not in {"pending", "running"}:
                cancel_event.set()
                return
            if task.last_heartbeat:
                elapsed = (
                    utc_now() - ensure_utc_aware(task.last_heartbeat)
                ).total_seconds()
                if elapsed > 60:
                    _publish_event(
                        task_id,
                        "error",
                        {"message": "frontend heartbeat timeout; duplicate task stopped"},
                        session_factory=session_factory,
                    )
                    cancel_event.set()
                    return


def _build_candidate_service(
    prepared: _PreparedDuplicateTask,
    snapshot: dict[str, Any],
    settings,
    embedding_service,
):
    descriptors = [_descriptor(document) for document in prepared.usable_documents]
    common = dict(
        embedding_service=embedding_service,
        semantic_enabled=feature(snapshot, "semantic"),
        semantic_min_score=threshold(
            snapshot, "semantic_min_score", settings.duplicate_semantic_min_score
        ),
        max_semantic_blocks=budget(
            snapshot, "embedding_max_blocks", settings.duplicate_embedding_max_blocks
        ),
        semantic_min_chars=budget(
            snapshot, "embedding_min_chars", settings.duplicate_embedding_min_chars
        ),
        candidate_min_score=threshold(snapshot, "candidate_min_score", 0.45),
        lexical_min_score=threshold(snapshot, "lexical_min_score", 0.16),
        structure_min_score=threshold(snapshot, "structure_min_score", 0.50),
        near_exact_min_score=threshold(snapshot, "near_exact_min_score", 0.72),
        image_min_score=threshold(snapshot, "image_min_score", 0.78),
        algorithm_version=str(snapshot.get("algorithm_version") or "duplicate-s2-4.1"),
    )
    if prepared.mode == "batch":
        return MultiDocumentCandidateService(
            descriptors,
            max_candidates=budget(
                snapshot, "batch_max_candidates", settings.duplicate_batch_max_candidates
            ),
            **common,
        )
    return DuplicateCandidateService(
        descriptors[0],
        descriptors[1],
        max_candidates=budget(
            snapshot, "pair_max_candidates", settings.duplicate_pair_max_candidates
        ),
        **common,
    )


@celery_app.task(bind=True, name="backend.tasks.duplicate_tasks.run_duplicate_check")
def run_duplicate_check(self, task_id: str) -> dict:
    """Execute one pair or batch duplicate task on the review worker queue."""

    async def _run() -> dict:
        settings = get_settings()
        session_factory, engine = create_session_factory()
        cancel_event = asyncio.Event()
        prepared: _PreparedDuplicateTask | None = None
        candidate_service = None
        try:
            async with session_factory() as db:
                task = (
                    await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
                ).scalar_one_or_none()
                if task is None or task.task_type != "duplicate":
                    return {"status": "error", "message": "duplicate task not found"}
                project = (
                    await db.execute(select(Project).where(Project.id == task.project_id))
                ).scalar_one_or_none()
                if project is None or project.project_type != "duplicate":
                    raise ValueError("duplicate project not found or has wrong type")
                documents = list(
                    (
                        await db.execute(
                            select(Document).where(Document.project_id == project.id)
                        )
                    ).scalars().all()
                )
                snapshot = snapshot_for_task(task, settings)
                prepared = _prepare_duplicate_task(task, project, documents)
                if prepared.mode == "batch" and not feature(snapshot, "batch"):
                    raise ValueError("batch duplicate mode is disabled by the release flag")

                # Defense in depth: direct/queued calls must not spend AI work
                # on byte-identical bidder documents.
                groups = await asyncio.to_thread(
                    find_identical_content_groups,
                    _hash_guard_items(prepared.bidder_documents),
                )
                if groups:
                    basis, ids, _digest = groups[0]
                    raise ValueError(
                        f"identical bidder documents detected ({basis}): {', '.join(ids)}"
                    )

                task.status = "running"
                task.started_at = utc_now()
                task.last_heartbeat = utc_now()
                task.duplicate_mode = prepared.mode
                task.duplicate_feature_snapshot = snapshot
                task.duplicate_algorithm_version = snapshot.get("algorithm_version")
                await db.commit()
                project_id = str(project.id)
                user_id = str(project.user_id)
                user = (
                    await db.execute(select(User).where(User.id == project.user_id))
                ).scalar_one_or_none()
                usage_identity = {
                    "external_user_id": user.external_user_id if user else None,
                    "local_user_id": user_id,
                    "user_name": (user.username if user else user_id) or user_id,
                    "enterprise_name": user.enterprise_name if user else None,
                    "interior_user": bool(user.interior_user) if user else False,
                }
                max_concurrency = task.max_concurrency

            _publish_event(task_id, "status", {"status": "running", "mode": prepared.mode})
            _publish_event(
                task_id,
                "progress",
                {
                    "message": "building duplicate candidate index",
                    "mode": prepared.mode,
                    "coverage_status": prepared.coverage_status,
                    "coverage_warnings": prepared.coverage_warnings,
                    "algorithm_version": snapshot.get("algorithm_version"),
                },
            )

            from backend.services.embedding_service import EmbeddingService
            from backend.services.usage_context import (
                UsageContext,
                reset_usage_context,
                set_usage_context,
            )

            embedding_service = EmbeddingService(
                enabled=feature(snapshot, "semantic"),
                batch_size=budget(
                    snapshot, "embedding_batch_size", settings.duplicate_embedding_batch_size
                ),
                timeout_seconds=decimal_budget(
                    snapshot,
                    "embedding_timeout_seconds",
                    settings.duplicate_embedding_timeout_seconds,
                ),
                max_input_chars=budget(
                    snapshot,
                    "embedding_max_input_chars",
                    settings.duplicate_embedding_max_input_chars,
                ),
            )
            candidate_service = _build_candidate_service(
                prepared, snapshot, settings, embedding_service
            )
            usage_token = set_usage_context(
                UsageContext(
                    **usage_identity,
                    project_id=project_id,
                    task_id=task_id,
                    todo_id=None,
                )
            )
            try:
                await candidate_service.build()
            finally:
                reset_usage_context(usage_token)

            coverage_status = prepared.coverage_status
            coverage_warnings = list(prepared.coverage_warnings)
            if candidate_service.warnings:
                coverage_status, combined_warnings = combine_coverage_summaries(
                    ({"status": coverage_status}, {"status": "partial"})
                )
                coverage_warnings = list(
                    dict.fromkeys(
                        [*coverage_warnings, *combined_warnings, *candidate_service.warnings]
                    )
                )

            source_index = DuplicateSourceIndex(
                _source_descriptor(document) for document in prepared.source_documents
            )
            source_block_count = await source_index.build()
            if source_index.warnings:
                coverage_status, combined_warnings = combine_coverage_summaries(
                    ({"status": coverage_status}, {"status": "partial"})
                )
                coverage_warnings = list(
                    dict.fromkeys(
                        [*coverage_warnings, *combined_warnings, *source_index.warnings]
                    )
                )

            cache_path = (
                settings.workspace_path
                / user_id
                / project_id
                / task_id
                / "duplicate_candidates.json"
            )
            candidate_service.save_cache(cache_path)
            if prepared.mode == "batch":
                await seed_duplicate_task_index(
                    session_factory,
                    task_id=task_id,
                    documents=prepared.bidder_documents,
                    candidate_service=candidate_service,
                    default_coverage_status=coverage_status,
                )
            _publish_event(
                task_id,
                "progress",
                {
                    "message": f"candidate index ready: {len(candidate_service.candidates)} candidates",
                    "mode": prepared.mode,
                    "source_block_count": source_block_count,
                    "source_warnings": source_index.warnings,
                    "candidate_warnings": candidate_service.warnings,
                    "semantic_enabled": feature(snapshot, "semantic"),
                },
            )

            def event_cb(event_type: str, data: dict):
                _publish_event(
                    task_id,
                    event_type,
                    data,
                    session_factory=session_factory,
                )

            master = DuplicateMasterAgent(
                project_id=project_id,
                task_id=task_id,
                user_id=user_id,
                rule_library_path=str(settings.duplicate_rule_library_path),
                left_document_id=prepared.left_document.id,
                right_document_id=prepared.right_document.id,
                candidate_service=candidate_service,
                source_index=source_index,
                session_factory=session_factory,
                max_concurrency=max_concurrency,
                event_callback=event_cb,
                cancel_event=cancel_event,
                coverage_status=coverage_status,
            )

            watchdog = asyncio.create_task(
                _progress_watchdog(task_id, cancel_event, operation_name="duplicate")
            )
            cancel_monitor = asyncio.create_task(
                _cancellation_monitor(session_factory, task_id, cancel_event)
            )
            try:
                try:
                    result = await asyncio.wait_for(
                        master.run(),
                        timeout=budget(
                            snapshot,
                            "agent_total_timeout_seconds",
                            settings.agent_total_timeout,
                        ),
                    )
                except asyncio.TimeoutError as exc:
                    cancel_event.set()
                    raise TimeoutError(
                        f"duplicate task timed out after {budget(snapshot, 'agent_total_timeout_seconds', settings.agent_total_timeout)} seconds"
                    ) from exc
            finally:
                watchdog.cancel()
                cancel_monitor.cancel()
                await asyncio.gather(watchdog, cancel_monitor, return_exceptions=True)

            if cancel_event.is_set() or is_task_cancelled(task_id):
                raise asyncio.CancelledError()
            if not result.get("success"):
                raise RuntimeError(result.get("error") or "duplicate master failed")
            if prepared.mode == "batch":
                await finalize_duplicate_task_matrix(
                    session_factory,
                    task_id=task_id,
                    candidate_service=candidate_service,
                )

            async with session_factory() as db:
                task = (
                    await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
                ).scalar_one()
                task.status = "completed"
                task.completed_at = utc_now()
                task.duration_seconds = utc_seconds_between(task.started_at, task.completed_at)
                await db.commit()

            from backend.services.billing import settle_review_consumption
            from backend.services.usage_summary import refresh_task_summary

            await refresh_task_summary(task_id)
            await settle_review_consumption(task_id)
            finding_count = int(result.get("stats", {}).get("finding_count", 0))
            _publish_event(
                task_id,
                "complete",
                {"status": "completed", "findings_count": finding_count, "mode": prepared.mode},
            )
            return {"status": "success", "finding_count": finding_count}

        except asyncio.CancelledError:
            async with session_factory() as db:
                task = (
                    await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
                ).scalar_one_or_none()
                if task:
                    task.status = "cancelled"
                    task.error_message = "duplicate task cancelled"
                    task.completed_at = utc_now()
                    if task.started_at:
                        task.duration_seconds = utc_seconds_between(
                            task.started_at, task.completed_at
                        )
                    await db.commit()
            _publish_event(task_id, "error", {"message": "duplicate task cancelled"})
            return {"status": "cancelled"}
        except Exception as exc:
            logger.exception("Duplicate task failed: task_id=%s", task_id)
            async with session_factory() as db:
                task = (
                    await db.execute(select(ReviewTask).where(ReviewTask.id == task_id))
                ).scalar_one_or_none()
                if task:
                    task.status = "failed"
                    task.error_message = str(exc)
                    task.completed_at = utc_now()
                    if task.started_at:
                        task.duration_seconds = utc_seconds_between(
                            task.started_at, task.completed_at
                        )
                    await db.commit()
            from backend.services.usage_summary import refresh_task_summary

            await refresh_task_summary(task_id)
            _publish_event(task_id, "error", {"message": str(exc)})
            return {"status": "error", "message": str(exc)}
        finally:
            clear_task_cancelled(task_id)
            await engine.dispose()

    return run_async(_run())


__all__ = ["run_duplicate_check"]
