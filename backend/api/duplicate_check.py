"""API routes for technical bid duplicate checking."""

from __future__ import annotations

import asyncio
import json
from itertools import combinations
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from backend.api.deps import CurrentUser, DBSession, is_interior_user
from backend.config import get_settings
from backend.models import (
    AgentStep,
    Document,
    DuplicateDocumentMember,
    DuplicateEvidenceCluster,
    DuplicateOccurrence,
    DuplicatePairSummary,
    DuplicateResult,
    Project,
    ReviewTask,
    TodoItem,
)
from backend.schemas.duplicate_check import (
    DuplicateClusterResponse,
    DuplicateCoverageResponse,
    DuplicateMatrixResponse,
    DuplicateOccurrenceResponse,
    DuplicateResultResponse,
    DuplicateResultsResponse,
    DuplicateSummary,
    DuplicateTodoResponse,
)
from backend.schemas.review import AgentStepResponse, ReviewTaskListItem, ReviewTaskResponse
from backend.services.duplicate_hash import find_identical_content_hash
from backend.services.duplicate_hash import find_identical_content_groups
from backend.services.duplicate_runtime import build_duplicate_feature_snapshot
from backend.services.document_artifacts import combine_coverage_summaries
from backend.services.document_artifacts import load_evidence_blocks
from backend.services.duplicate_sources import (
    DuplicateSourceIndex,
    SourceDocumentDescriptor,
)
from backend.services.duplicate_tables import compare_table_blocks
from backend.services.sse_service import sse_manager
from backend.utils.time_utils import utc_now

router = APIRouter(
    prefix="/projects/{project_id}/duplicate-check", tags=["Duplicate Check"]
)
capabilities_router = APIRouter(
    prefix="/duplicate-check", tags=["Duplicate Check"]
)

BLOCKED_EXTERNAL_EVENTS = {
    "step",
    "sub_agent_step",
    "sub_agent_step_start",
    "sub_agent_llm_output",
    "sub_agent_tool_call_start",
    "sub_agent_tool_call_end",
    "sub_agent_step_complete",
}

_DUPLICATE_DOCUMENT_TYPES = {"duplicate_left", "duplicate_right", "duplicate_bid"}
_DUPLICATE_SOURCE_TYPES = {"duplicate_tender", "duplicate_public_reference"}


@capabilities_router.get("/capabilities")
async def get_duplicate_release_capabilities(current_user: CurrentUser) -> dict:
    """Expose customer-visible duplicate modes from the runtime release flags."""

    snapshot = build_duplicate_feature_snapshot(get_settings())
    return {
        "algorithm_version": snapshot["algorithm_version"],
        "features": snapshot["features"],
    }


def _duplicate_mode(project: Project) -> str:
    value = getattr(project, "duplicate_mode", None) or "pair"
    return value if value in {"pair", "batch"} else "pair"


def _ordered_bid_documents(documents: list[Document]) -> list[Document]:
    return sorted(
        [document for document in documents if document.doc_type == "duplicate_bid"],
        key=lambda document: (
            getattr(document, "duplicate_ordinal", None)
            if getattr(document, "duplicate_ordinal", None) is not None
            else 999,
            getattr(document, "created_at", None),
            document.original_filename,
        ),
    )


def _location_with_preview(location: dict | None, document) -> dict:
    payload = dict(location or {})
    image_name = payload.get("image_path")
    images_dir_value = getattr(document, "parsed_images_dir", None) if document else None
    if not image_name or not images_dir_value:
        return payload
    try:
        workspace = get_settings().workspace_path.resolve()
        image_path = (Path(images_dir_value) / Path(str(image_name)).name).resolve()
        image_path.relative_to(workspace)
        if image_path.is_file():
            payload["preview_url"] = f"/files/{image_path.relative_to(workspace).as_posix()}"
            payload["thumbnail_url"] = payload["preview_url"]
    except Exception:
        pass
    return payload


def _coverage_inputs_from_documents(documents) -> list[dict | None]:
    """Extract coverage summaries while tolerating lightweight test doubles."""

    inputs: list[dict | None] = []
    for document in documents:
        if isinstance(document, dict):
            if document.get("doc_type") in _DUPLICATE_DOCUMENT_TYPES:
                inputs.append(document.get("coverage_summary"))
        elif (
            getattr(document, "doc_type", None) in _DUPLICATE_DOCUMENT_TYPES
            and hasattr(document, "coverage_summary")
        ):
            inputs.append(getattr(document, "coverage_summary", None))
    return inputs


async def _project(
    project_id: str,
    current_user,
    db: DBSession,
    *,
    allow_interior_read: bool = False,
) -> Project:
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None or project.project_type != "duplicate":
        raise HTTPException(status_code=404, detail="查重项目不存在或无权访问")
    if allow_interior_read and is_interior_user(current_user):
        return project
    if project.user_id != current_user.id or project.is_deleted:
        raise HTTPException(status_code=404, detail="查重项目不存在或无权访问")
    return project


async def _task(project_id: str, task_id: str, db: DBSession) -> ReviewTask:
    task = (
        await db.execute(
            select(ReviewTask).where(
                ReviewTask.id == task_id,
                ReviewTask.project_id == project_id,
                ReviewTask.task_type == "duplicate",
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="查重任务不存在或已被删除")
    return task


def _source_index_from_documents(documents) -> DuplicateSourceIndex:
    return DuplicateSourceIndex(
        SourceDocumentDescriptor(
            id=document.id,
            filename=document.original_filename,
            evidence_blocks_path=document.evidence_blocks_path,
            source_basis=(
                "tender" if document.doc_type == "duplicate_tender" else "public"
            ),
            snapshot_hash=document.source_snapshot_hash,
            version=document.source_version,
            source_uri=document.source_uri,
        )
        for document in documents
        if document.doc_type in _DUPLICATE_SOURCE_TYPES
    )


@router.get("/capabilities")
async def get_duplicate_capabilities(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """Expose the effective release switches without leaking credentials."""

    project = await _project(project_id, current_user, db, allow_interior_read=True)
    snapshot = build_duplicate_feature_snapshot(get_settings())
    return {
        "project_mode": _duplicate_mode(project),
        "algorithm_version": snapshot["algorithm_version"],
        "features": snapshot["features"],
        "thresholds": snapshot["thresholds"],
        "budgets": snapshot["budgets"] if is_interior_user(current_user) else None,
    }


@router.get("/sources")
async def list_duplicate_sources(
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    """List immutable source snapshots without exposing workspace paths."""

    await _project(project_id, current_user, db, allow_interior_read=True)
    documents = list(
        (
            await db.execute(
                select(Document)
                .where(
                    Document.project_id == project_id,
                    Document.doc_type.in_(_DUPLICATE_SOURCE_TYPES),
                )
                .order_by(Document.created_at.asc())
            )
        ).scalars().all()
    )
    return {
        "sources": [
            {
                "document_id": document.id,
                "filename": document.original_filename,
                "source_basis": (
                    "tender"
                    if document.doc_type == "duplicate_tender"
                    else "public"
                ),
                "version": document.source_version,
                "snapshot_hash": document.source_snapshot_hash,
                "source_uri": document.source_uri,
                "status": document.status,
                "coverage": document.coverage_summary,
            }
            for document in documents
        ]
    }


@router.get("/tasks/{task_id}/sources/search")
async def search_duplicate_sources(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
    query: str = Query(..., min_length=1, max_length=4000),
    source_basis: str | None = Query(default=None, pattern="^(tender|public)$"),
    limit: int = Query(default=12, ge=1, le=50),
) -> dict:
    await _project(project_id, current_user, db, allow_interior_read=True)
    await _task(project_id, task_id, db)
    documents = list(
        (
            await db.execute(select(Document).where(Document.project_id == project_id))
        ).scalars().all()
    )
    index = _source_index_from_documents(documents)
    await index.build()
    return {
        "sources": [
            item.to_agent_dict()
            for item in index.search(query, source_basis=source_basis, limit=limit)
        ],
        "warnings": index.warnings,
    }


@router.get("/tasks/{task_id}/sources/{source_reference_id}")
async def get_duplicate_source_context(
    project_id: str,
    task_id: str,
    source_reference_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    await _project(project_id, current_user, db, allow_interior_read=True)
    await _task(project_id, task_id, db)
    documents = list(
        (
            await db.execute(select(Document).where(Document.project_id == project_id))
        ).scalars().all()
    )
    index = _source_index_from_documents(documents)
    await index.build()
    payload = index.get_context(source_reference_id, radius=1)
    if payload is None:
        raise HTTPException(status_code=404, detail="来源证据不存在")
    return payload


@router.get("/tasks/{task_id}/tables")
async def get_duplicate_table_comparisons(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """Return row/column comparisons instead of flattened Markdown only."""

    await _project(project_id, current_user, db, allow_interior_read=True)
    task = await _task(project_id, task_id, db)
    documents = list(
        (
            await db.execute(select(Document).where(Document.project_id == project_id))
        ).scalars().all()
    )
    if getattr(task, "duplicate_mode", "pair") == "batch":
        comparisons = []
        warnings: list[str] = []
        bidders = _ordered_bid_documents(documents)
        per_pair_limit = max(1, min(100, limit // max(1, len(bidders) - 1)))
        for left_document, right_document in combinations(bidders, 2):
            left_blocks = load_evidence_blocks(left_document.evidence_blocks_path)
            right_blocks = load_evidence_blocks(right_document.evidence_blocks_path)
            if not left_blocks:
                warnings.append(f"table_evidence_unavailable:{left_document.id}")
            if not right_blocks:
                warnings.append(f"table_evidence_unavailable:{right_document.id}")
            comparisons.extend(
                item.to_dict()
                for item in compare_table_blocks(
                    left_blocks, right_blocks, limit=per_pair_limit
                )
            )
        comparisons.sort(key=lambda item: item["score"], reverse=True)
        return {
            "comparisons": comparisons[:limit],
            "warnings": list(dict.fromkeys(warnings)),
        }
    left = next((doc for doc in documents if doc.doc_type == "duplicate_left"), None)
    right = next((doc for doc in documents if doc.doc_type == "duplicate_right"), None)
    if left is None or right is None:
        raise HTTPException(status_code=400, detail="查重项目缺少 A/B 文档")
    left_blocks = load_evidence_blocks(left.evidence_blocks_path)
    right_blocks = load_evidence_blocks(right.evidence_blocks_path)
    comparisons = compare_table_blocks(left_blocks, right_blocks, limit=limit)
    warnings = []
    if not left.evidence_blocks_path or not left_blocks:
        warnings.append(f"table_evidence_unavailable:{left.id}")
    if not right.evidence_blocks_path or not right_blocks:
        warnings.append(f"table_evidence_unavailable:{right.id}")
    return {
        "comparisons": [item.to_dict() for item in comparisons],
        "warnings": warnings,
    }


async def _duplicate_task_documents(project_id: str, db: DBSession) -> list[Document]:
    return list(
        (
            await db.execute(
                select(Document).where(Document.project_id == project_id)
            )
        ).scalars().all()
    )


def _coverage_document_payload(document: Document) -> dict:
    summary = document.coverage_summary
    return {
        "document_id": document.id,
        "filename": document.original_filename,
        "doc_type": document.doc_type,
        "status": document.status,
        "coverage_status": (
            summary.get("status", "insufficient")
            if isinstance(summary, dict)
            else "insufficient"
        ),
        "coverage_summary": summary,
    }


@router.get("/tasks/{task_id}/coverage", response_model=DuplicateCoverageResponse)
async def get_duplicate_coverage(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    project = await _project(project_id, current_user, db, allow_interior_read=True)
    task = await _task(project_id, task_id, db)
    documents = await _duplicate_task_documents(project_id, db)
    relevant = [
        document
        for document in documents
        if document.doc_type in (_DUPLICATE_DOCUMENT_TYPES | _DUPLICATE_SOURCE_TYPES)
    ]
    status_value, warnings = combine_coverage_summaries(
        document.coverage_summary for document in relevant
    )
    for document in relevant:
        if document.status != "parsed":
            warnings.append(f"{document.original_filename}: parse_status_{document.status}")
    warnings = list(dict.fromkeys(warnings))
    return {
        "task_id": task.id,
        "mode": getattr(task, "duplicate_mode", None) or _duplicate_mode(project),
        "coverage_status": status_value,
        "coverage_warnings": warnings,
        "algorithm_version": getattr(task, "duplicate_algorithm_version", None),
        "feature_snapshot": getattr(task, "duplicate_feature_snapshot", None),
        "documents": [_coverage_document_payload(document) for document in relevant],
    }


@router.get("/tasks/{task_id}/matrix", response_model=DuplicateMatrixResponse)
async def get_duplicate_matrix(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    project = await _project(project_id, current_user, db, allow_interior_read=True)
    task = await _task(project_id, task_id, db)
    documents = await _duplicate_task_documents(project_id, db)
    document_by_id = {document.id: document for document in documents}
    members = list(
        (
            await db.execute(
                select(DuplicateDocumentMember)
                .where(DuplicateDocumentMember.task_id == task_id)
                .order_by(DuplicateDocumentMember.ordinal.asc())
            )
        ).scalars().all()
    )
    pairs = list(
        (
            await db.execute(
                select(DuplicatePairSummary)
                .where(DuplicatePairSummary.task_id == task_id)
                .order_by(
                    DuplicatePairSummary.left_document_id,
                    DuplicatePairSummary.right_document_id,
                )
            )
        ).scalars().all()
    )
    # Pair tasks created before S2-3 have no membership rows.  Return a
    # read-only compatibility matrix synthesized from their two documents.
    if not members:
        bidder_documents = [
            document
            for document in documents
            if document.doc_type in {"duplicate_left", "duplicate_right", "duplicate_bid"}
        ]
        bidder_documents.sort(
            key=lambda document: (
                0 if document.doc_type == "duplicate_left" else 1,
                (
                    getattr(document, "duplicate_ordinal", None)
                    if getattr(document, "duplicate_ordinal", None) is not None
                    else 999
                ),
            )
        )
        members_payload = [
            {
                "task_id": task_id,
                "document_id": document.id,
                "party_key": getattr(document, "duplicate_party_key", None)
                or ("A" if index == 0 else "B" if index == 1 else f"party-{index + 1}"),
                "display_name": getattr(document, "duplicate_display_name", None)
                or document.original_filename,
                "ordinal": index,
                "metadata": {"doc_type": document.doc_type},
                "filename": document.original_filename,
                "status": document.status,
                "coverage_status": (
                    document.coverage_summary.get("status", "insufficient")
                    if isinstance(document.coverage_summary, dict)
                    else "insufficient"
                ),
            }
            for index, document in enumerate(bidder_documents)
        ]
    else:
        members_payload = [
            {
                "task_id": member.task_id,
                "document_id": member.document_id,
                "party_key": member.party_key,
                "display_name": member.display_name,
                "ordinal": member.ordinal,
                "metadata": member.member_metadata,
                "filename": document_by_id.get(member.document_id).original_filename
                if document_by_id.get(member.document_id)
                else None,
                "status": document_by_id.get(member.document_id).status
                if document_by_id.get(member.document_id)
                else None,
                "coverage_status": (
                    document_by_id[member.document_id].coverage_summary.get(
                        "status", "insufficient"
                    )
                    if document_by_id.get(member.document_id)
                    and isinstance(document_by_id[member.document_id].coverage_summary, dict)
                    else "insufficient"
                ),
            }
            for member in members
        ]
    member_names = {
        item["document_id"]: item["display_name"] for item in members_payload
    }
    if not pairs and len(members_payload) >= 2:
        legacy_findings = list(
            (
                await db.execute(
                    select(DuplicateResult).where(DuplicateResult.task_id == task_id)
                )
            ).scalars().all()
        )
        by_pair: dict[tuple[str, str], list[DuplicateResult]] = {}
        for finding in legacy_findings:
            pair_key = tuple(
                sorted((finding.left_document_id, finding.right_document_id))
            )
            by_pair.setdefault(pair_key, []).append(finding)
        synthetic_pairs = []
        for pair_key, pair_findings in by_pair.items():
            synthetic_pairs.append(
                {
                    "id": f"legacy:{task_id}:{pair_key[0]}:{pair_key[1]}",
                    "task_id": task_id,
                    "left_document_id": pair_key[0],
                    "right_document_id": pair_key[1],
                    "left_display_name": member_names.get(pair_key[0]),
                    "right_display_name": member_names.get(pair_key[1]),
                    "candidate_count": 0,
                    "finding_count": len(pair_findings),
                    "suspicious_count": sum(
                        item.verdict == "suspicious" for item in pair_findings
                    ),
                    "unknown_count": sum(
                        item.verdict == "unknown" for item in pair_findings
                    ),
                    "max_evidence_strength": max(
                        (
                            float(item.confidence)
                            for item in pair_findings
                            if getattr(item, "confidence", None) is not None
                        ),
                        default=None,
                    ),
                    "coverage_status": min(
                        (
                            getattr(item, "coverage_status", "insufficient")
                            for item in pair_findings
                        ),
                        key=lambda value: {"insufficient": 0, "partial": 1, "complete": 2}.get(value, 0),
                        default="insufficient",
                    ),
                    "channel_hits": None,
                }
            )
    else:
        synthetic_pairs = []
    status_value, warnings = combine_coverage_summaries(
        document.coverage_summary
        for document in documents
        if document.doc_type in (_DUPLICATE_DOCUMENT_TYPES | _DUPLICATE_SOURCE_TYPES)
    )
    return {
        "task_id": task_id,
        "mode": getattr(task, "duplicate_mode", None) or _duplicate_mode(project),
        "coverage_status": status_value,
        "coverage_warnings": warnings,
        "members": members_payload,
        "pairs": synthetic_pairs or [
            {
                "id": pair.id,
                "task_id": pair.task_id,
                "left_document_id": pair.left_document_id,
                "right_document_id": pair.right_document_id,
                "left_display_name": member_names.get(pair.left_document_id),
                "right_display_name": member_names.get(pair.right_document_id),
                "candidate_count": pair.candidate_count,
                "finding_count": pair.finding_count,
                "suspicious_count": pair.suspicious_count,
                "unknown_count": pair.unknown_count,
                "max_evidence_strength": pair.max_evidence_strength,
                "coverage_status": pair.coverage_status,
                "channel_hits": pair.channel_hits,
            }
            for pair in pairs
        ],
    }


@router.get("/tasks/{task_id}/clusters", response_model=list[DuplicateClusterResponse])
async def get_duplicate_clusters(
    project_id: str,
    task_id: str,
    db: DBSession,
    current_user: CurrentUser,
    include_occurrences: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict]:
    await _project(project_id, current_user, db, allow_interior_read=True)
    await _task(project_id, task_id, db)
    documents = await _duplicate_task_documents(project_id, db)
    document_by_id = {document.id: document for document in documents}
    members = list(
        (
            await db.execute(
                select(DuplicateDocumentMember).where(
                    DuplicateDocumentMember.task_id == task_id
                )
            )
        ).scalars().all()
    )
    member_names = {member.document_id: member.display_name for member in members}
    for document in documents:
        member_names.setdefault(
            document.id,
            getattr(document, "duplicate_display_name", None)
            or document.original_filename,
        )
    clusters = list(
        (
            await db.execute(
                select(DuplicateEvidenceCluster)
                .where(DuplicateEvidenceCluster.task_id == task_id)
                .order_by(DuplicateEvidenceCluster.evidence_strength.desc())
                .limit(limit)
            )
        ).scalars().all()
    )
    occurrences_by_cluster: dict[str, list[DuplicateOccurrence]] = {}
    if include_occurrences and clusters:
        rows = list(
            (
                await db.execute(
                    select(DuplicateOccurrence).where(
                        DuplicateOccurrence.cluster_id.in_([cluster.id for cluster in clusters])
                    )
                )
            ).scalars().all()
        )
        for occurrence in rows:
            occurrences_by_cluster.setdefault(occurrence.cluster_id, []).append(occurrence)

    def occurrence_payload(occurrence: DuplicateOccurrence) -> dict:
        document = document_by_id.get(occurrence.document_id)
        location = _location_with_preview(
            occurrence.location, document
        )
        return {
            "id": occurrence.id,
            "task_id": occurrence.task_id,
            "finding_id": occurrence.finding_id,
            "cluster_id": occurrence.cluster_id,
            "document_id": occurrence.document_id,
            "filename": document.original_filename if document else None,
            "display_name": member_names.get(occurrence.document_id),
            "block_id": occurrence.block_id,
            "excerpt": occurrence.excerpt,
            "location": location,
            "channel": occurrence.channel,
        }

    return [
        {
            "id": cluster.id,
            "task_id": cluster.task_id,
            "finding_id": cluster.finding_id,
            "cluster_key": cluster.cluster_key,
            "content_type": cluster.content_type,
            "document_ids": cluster.document_ids,
            "occurrence_count": cluster.occurrence_count,
            "representative_excerpt": cluster.representative_excerpt,
            "evidence_strength": cluster.evidence_strength,
            "coverage_status": cluster.coverage_status,
            "metadata": cluster.cluster_metadata,
            "occurrences": [
                occurrence_payload(item)
                for item in occurrences_by_cluster.get(cluster.id, [])
            ],
        }
        for cluster in clusters
    ]


@router.get(
    "/tasks/{task_id}/findings/{finding_id}/occurrences",
    response_model=list[DuplicateOccurrenceResponse],
)
async def get_duplicate_finding_occurrences(
    project_id: str,
    task_id: str,
    finding_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> list[dict]:
    await _project(project_id, current_user, db, allow_interior_read=True)
    await _task(project_id, task_id, db)
    finding = (
        await db.execute(
            select(DuplicateResult).where(
                DuplicateResult.id == finding_id,
                DuplicateResult.task_id == task_id,
            )
        )
    ).scalar_one_or_none()
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    documents = await _duplicate_task_documents(project_id, db)
    document_by_id = {document.id: document for document in documents}
    members = list(
        (
            await db.execute(
                select(DuplicateDocumentMember).where(
                    DuplicateDocumentMember.task_id == task_id
                )
            )
        ).scalars().all()
    )
    member_names = {member.document_id: member.display_name for member in members}
    for document in documents:
        member_names.setdefault(
            document.id,
            getattr(document, "duplicate_display_name", None)
            or document.original_filename,
        )
    rows = list(
        (
            await db.execute(
                select(DuplicateOccurrence)
                .where(DuplicateOccurrence.finding_id == finding_id)
                .order_by(DuplicateOccurrence.created_at.asc())
            )
        ).scalars().all()
    )
    return [
        {
            "id": row.id,
            "task_id": row.task_id,
            "finding_id": row.finding_id,
            "cluster_id": row.cluster_id,
            "document_id": row.document_id,
            "filename": document_by_id[row.document_id].original_filename
            if row.document_id in document_by_id
            else None,
            "display_name": member_names.get(row.document_id),
            "block_id": row.block_id,
            "excerpt": row.excerpt,
            "location": _location_with_preview(
                row.location, document_by_id.get(row.document_id)
            ),
            "channel": row.channel,
        }
        for row in rows
    ]


@router.post("", response_model=ReviewTaskResponse, status_code=status.HTTP_201_CREATED)
async def start_duplicate_check(
    request: Request,
    project_id: str,
    db: DBSession,
    current_user: CurrentUser,
) -> ReviewTask:
    project = await _project(project_id, current_user, db)
    documents = list(
        (
            await db.execute(select(Document).where(Document.project_id == project_id))
        ).scalars().all()
    )
    mode = _duplicate_mode(project)
    settings = get_settings()
    if mode == "batch":
        if not settings.duplicate_batch_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "DUPLICATE_BATCH_DISABLED",
                    "message": "批量查重功能尚未开启，请联系管理员",
                },
            )
        bidder_documents = _ordered_bid_documents(documents)
        if not 3 <= len(bidder_documents) <= 10:
            raise HTTPException(status_code=400, detail="批量查重需要 3-10 份 duplicate_bid 文档")
        usable = [
            document
            for document in bidder_documents
            if document.status == "parsed"
            and (document.parsed_markdown_path or document.parsed_html_path)
            and Path(document.parsed_markdown_path or document.parsed_html_path).exists()
        ]
        if len(usable) < 2:
            raise HTTPException(status_code=400, detail="批量查重至少需要两份已解析文档")
        groups = await asyncio.to_thread(
            find_identical_content_groups,
            [
                (
                    document.id,
                    document.file_path,
                    document.parsed_markdown_path or document.parsed_html_path,
                )
                for document in bidder_documents
            ],
        )
        if groups:
            basis, document_ids, _digest = groups[0]
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "IDENTICAL_DOCUMENTS",
                    "message": f"批量文档存在内容完全相同的文件（{basis}）：{', '.join(document_ids)}",
                    "document_ids": document_ids,
                },
            )
        # Keep the existing pair validation/billing path below while the
        # worker receives the authoritative batch mode from the task row.
        left = [usable[0]]
        right = [usable[1]]
    else:
        left = [d for d in documents if d.doc_type == "duplicate_left"]
        right = [d for d in documents if d.doc_type == "duplicate_right"]
    if len(left) != 1 or len(right) != 1:
        raise HTTPException(status_code=400, detail="请分别上传一份 A 方和 B 方技术应标书")
    if any(document.status != "parsed" for document in (left[0], right[0])):
        raise HTTPException(status_code=400, detail="两份技术应标书必须全部解析完成")

    identical = await asyncio.to_thread(
        find_identical_content_hash,
        left[0].file_path,
        right[0].file_path,
        left[0].parsed_markdown_path or left[0].parsed_html_path,
        right[0].parsed_markdown_path or right[0].parsed_html_path,
    )
    if identical:
        basis, _digest = identical
        basis_text = "原始上传文件" if basis == "original" else "解析内容"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "IDENTICAL_DOCUMENTS",
                "message": f"两份技术应标书的{basis_text}内容完全相同，无需发起 AI 查重",
            },
        )

    from backend.services.task_lifecycle import (
        add_task_dispatch,
        authorize_billable_task_start,
        dispatch_task_outbox,
    )

    sales_config = await authorize_billable_task_start(
        db, user_id=current_user.id, operation_name=" AI 查重"
    )

    from backend.api.deps import get_token_claims, oauth2_scheme

    token = await oauth2_scheme(request)
    claims = get_token_claims(token)
    concurrency = (
        claims.get("concurrency") or get_settings().max_sub_agent_concurrency
    )
    feature_snapshot = build_duplicate_feature_snapshot(settings)
    task = ReviewTask(
        project_id=project.id,
        task_type="duplicate",
        duplicate_mode=mode,
        duplicate_feature_snapshot=feature_snapshot,
        duplicate_algorithm_version=feature_snapshot.get("algorithm_version"),
        status="pending",
        max_concurrency=max(1, int(concurrency)),
        billing_multiplier=sales_config.sales_multiplier,
        billing_status="pending",
    )
    db.add(task)
    await db.flush()
    outbox = add_task_dispatch(db, task_kind="duplicate", task_id=task.id)
    await db.flush()
    task.celery_task_id = outbox.celery_task_id
    await db.commit()
    await db.refresh(task)
    await dispatch_task_outbox(outbox.id)
    return task


@router.get("/tasks", response_model=list[ReviewTaskListItem])
async def list_duplicate_tasks(
    project_id: str, db: DBSession, current_user: CurrentUser
) -> list[ReviewTask]:
    await _project(project_id, current_user, db, allow_interior_read=True)
    return list(
        (
            await db.execute(
                select(ReviewTask)
                .where(
                    ReviewTask.project_id == project_id,
                    ReviewTask.task_type == "duplicate",
                )
                .order_by(ReviewTask.created_at.desc())
            )
        ).scalars().all()
    )


@router.get("/tasks/{task_id}", response_model=ReviewTaskResponse)
async def get_duplicate_task(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> ReviewTask:
    await _project(project_id, current_user, db, allow_interior_read=True)
    return await _task(project_id, task_id, db)


@router.post("/tasks/{task_id}/cancel", response_model=ReviewTaskResponse)
async def cancel_duplicate_task(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> ReviewTask:
    await _project(project_id, current_user, db)
    task = await _task(project_id, task_id, db)
    if task.status not in {"pending", "running"}:
        raise HTTPException(status_code=400, detail="当前任务状态不可取消")
    from backend.services.task_lifecycle import (
        cancel_pending_dispatch,
        enqueue_billing_settlement,
        finalize_task_usage,
    )

    cancelled_before_dispatch = await cancel_pending_dispatch(
        db, task_kind="duplicate", task_id=task_id
    )
    if task.celery_task_id and not cancelled_before_dispatch:
        from backend.celery_app import celery_app

        try:
            celery_app.control.revoke(task.celery_task_id, terminate=False)
        except Exception:
            pass
    from backend.tasks.review_tasks import set_task_cancelled

    try:
        set_task_cancelled(task_id)
    except Exception:
        # The duplicate worker also polls the durable task status.
        pass
    task.status = "cancelled"
    task.completed_at = utc_now()
    task.billing_status = "pending"
    if cancelled_before_dispatch:
        task.usage_finalized_at = task.completed_at
    await db.commit()
    await db.refresh(task)
    if cancelled_before_dispatch:
        await finalize_task_usage("duplicate", task_id)
    else:
        enqueue_billing_settlement(
            "duplicate",
            task_id,
            countdown=get_settings().billing_orphan_finalize_grace_seconds,
        )
    return task


@router.post("/tasks/{task_id}/heartbeat")
async def heartbeat_duplicate_task(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> dict:
    await _project(project_id, current_user, db)
    task = await _task(project_id, task_id, db)
    if task.status != "running":
        return {"status": task.status, "message": "任务当前未在运行"}
    task.last_heartbeat = utc_now()
    await db.flush()
    return {"status": "ok", "last_heartbeat": task.last_heartbeat}


@router.get("/tasks/{task_id}/steps", response_model=list[AgentStepResponse])
async def get_duplicate_steps(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> list[AgentStep]:
    if not is_interior_user(current_user):
        raise HTTPException(status_code=403, detail="外部用户无权查看时间线")
    await _project(project_id, current_user, db, allow_interior_read=True)
    await _task(project_id, task_id, db)
    return list(
        (
            await db.execute(
                select(AgentStep)
                .where(AgentStep.task_id == task_id)
                .order_by(AgentStep.step_number.asc(), AgentStep.created_at.asc())
            )
        ).scalars().all()
    )


@router.get("/tasks/{task_id}/todos", response_model=list[DuplicateTodoResponse])
async def get_duplicate_todos(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> list[TodoItem]:
    await _project(project_id, current_user, db, allow_interior_read=True)
    await _task(project_id, task_id, db)
    return list(
        (
            await db.execute(
                select(TodoItem)
                .where(TodoItem.session_id == task_id)
                .order_by(TodoItem.created_at.asc())
            )
        ).scalars().all()
    )


@router.get("/tasks/{task_id}/results", response_model=DuplicateResultsResponse)
async def get_duplicate_results(
    project_id: str, task_id: str, db: DBSession, current_user: CurrentUser
) -> DuplicateResultsResponse:
    await _project(project_id, current_user, db, allow_interior_read=True)
    task = await _task(project_id, task_id, db)
    todos = list(
        (
            await db.execute(
                select(TodoItem)
                .where(TodoItem.session_id == task_id)
                .order_by(TodoItem.created_at.asc())
            )
        ).scalars().all()
    )
    findings = list(
        (
            await db.execute(
                select(DuplicateResult)
                .where(DuplicateResult.task_id == task_id)
                .order_by(DuplicateResult.rule_doc_name, DuplicateResult.created_at)
            )
        ).scalars().all()
    )
    finding_coverage = [getattr(finding, "coverage_status", None) for finding in findings]
    # ORM rows always expose the new column, including historical rows that
    # were backfilled by the migration.  In that case the linked documents are
    # authoritative: a historical ``complete`` default must not hide missing
    # parser coverage.  Lightweight legacy test doubles do not expose the
    # attribute, so retain the no-extra-query compatibility path.
    has_persisted_coverage = any(
        hasattr(finding, "coverage_status") for finding in findings
    )
    documents_for_coverage = []
    if not findings or has_persisted_coverage:
        documents_for_coverage = list(
            (
                await db.execute(select(Document).where(Document.project_id == project_id))
            ).scalars().all()
        )
    coverage_inputs = _coverage_inputs_from_documents(documents_for_coverage)
    coverage_available = any(item is not None for item in coverage_inputs)
    if coverage_available:
        coverage_status, coverage_warnings = combine_coverage_summaries(coverage_inputs)
        finding_statuses = [
            status
            for status in finding_coverage
            if status in {"complete", "partial", "insufficient"}
        ]
        if finding_statuses:
            finding_status, _ = combine_coverage_summaries(
                {"status": value} for value in finding_statuses
            )
            coverage_status, _ = combine_coverage_summaries(
                ({"status": coverage_status}, {"status": finding_status})
            )
    elif findings:
        # Results written before S2-0 (or rows whose document coverage was
        # purged) have no reliable parser coverage.  Keep the result visible,
        # but explicitly downgrade the task summary.
        coverage_status, coverage_warnings = "partial", ["旧任务未记录解析覆盖度"]
    else:
        coverage_status, coverage_warnings = combine_coverage_summaries(())
    document_ids = {
        finding.left_document_id for finding in findings
    } | {finding.right_document_id for finding in findings}
    filenames = {}
    if document_ids:
        rows = await db.execute(
            select(Document.id, Document.original_filename).where(Document.id.in_(document_ids))
        )
        filenames = {doc_id: filename for doc_id, filename in rows.all()}
    response_findings = []
    documents_by_id = {
        document.id: document
        for document in documents_for_coverage
        if getattr(document, "id", None)
    }
    for finding in findings:
        update = {
            "left_filename": filenames.get(finding.left_document_id),
            "right_filename": filenames.get(finding.right_document_id),
            "left_location": _location_with_preview(
                finding.left_location,
                documents_by_id.get(finding.left_document_id),
            ),
            "right_location": _location_with_preview(
                finding.right_location,
                documents_by_id.get(finding.right_document_id),
            ),
        }
        # A finding cannot claim complete coverage when the task-level parser
        # manifest says otherwise.  This also corrects old rows whose database
        # default predates S2-0.
        if coverage_status != "complete":
            update["coverage_status"] = coverage_status
        response_findings.append(
            DuplicateResultResponse.model_validate(finding).model_copy(update=update)
        )
    reasonable_count = sum(item.verdict == "reasonable" for item in findings)
    suspicious_count = sum(item.verdict == "suspicious" for item in findings)
    unknown_count = sum(item.verdict == "unknown" for item in findings)
    summary = DuplicateSummary(
        rule_count=len(todos),
        completed_rule_count=sum(todo.status == "completed" for todo in todos),
        reasonable_count=reasonable_count,
        suspicious_count=suspicious_count,
        unknown_count=unknown_count,
        coverage_status=coverage_status,
        coverage_warnings=coverage_warnings,
    )
    return DuplicateResultsResponse(summary=summary, findings=response_findings, todos=todos)


@router.get("/tasks/{task_id}/stream")
async def stream_duplicate_events(
    project_id: str,
    task_id: str,
    request: Request,
    db: DBSession,
    current_user: CurrentUser,
):
    await _project(project_id, current_user, db)
    await _task(project_id, task_id, db)
    internal = is_interior_user(current_user)
    last_event_id = request.headers.get("Last-Event-ID")

    async def generator():
        async for event in sse_manager.connect(task_id, last_event_id):
            if internal:
                yield event
                continue
            blocked = False
            for line in event.splitlines():
                if line.startswith("data: "):
                    try:
                        blocked = json.loads(line[6:]).get("type") in BLOCKED_EXTERNAL_EVENTS
                    except json.JSONDecodeError:
                        blocked = False
                    break
            if not blocked:
                yield event

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
