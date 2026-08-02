"""Persistence helpers for duplicate task membership, clusters and matrix."""

from __future__ import annotations

from itertools import combinations
from hashlib import sha256

from sqlalchemy import delete, select

from backend.models import (
    DuplicateDocumentMember,
    DuplicateEvidenceCluster,
    DuplicateOccurrence,
    DuplicatePairSummary,
    DuplicateResult,
)
from backend.services.document_artifacts import combine_coverage_summaries


async def seed_duplicate_task_index(
    session_factory,
    *,
    task_id: str,
    documents: list,
    candidate_service,
    default_coverage_status: str,
) -> None:
    """Create task members, all document pairs and exact evidence clusters."""

    ordered = sorted(
        documents,
        key=lambda document: (
            getattr(document, "duplicate_ordinal", None)
            if getattr(document, "duplicate_ordinal", None) is not None
            else 999,
            document.original_filename,
        ),
    )
    async with session_factory() as db:
        # Retry-safe cleanup is task-scoped and leaves DuplicateResult rows to
        # the master transaction semantics.
        await db.execute(
            delete(DuplicateOccurrence).where(
                DuplicateOccurrence.task_id == task_id,
                DuplicateOccurrence.finding_id.is_(None),
            )
        )
        await db.execute(delete(DuplicateEvidenceCluster).where(DuplicateEvidenceCluster.task_id == task_id))
        await db.execute(delete(DuplicatePairSummary).where(DuplicatePairSummary.task_id == task_id))
        await db.execute(delete(DuplicateDocumentMember).where(DuplicateDocumentMember.task_id == task_id))

        for index, document in enumerate(ordered):
            db.add(
                DuplicateDocumentMember(
                    task_id=task_id,
                    document_id=document.id,
                    party_key=(
                        getattr(document, "duplicate_party_key", None)
                        or ("A" if index == 0 else "B" if index == 1 else f"party-{index + 1}")
                    ),
                    display_name=(
                        getattr(document, "duplicate_display_name", None)
                        or document.original_filename
                    ),
                    ordinal=(
                        getattr(document, "duplicate_ordinal", None)
                        if getattr(document, "duplicate_ordinal", None) is not None
                        else index
                    ),
                    member_metadata={
                        "doc_type": document.doc_type,
                        "coverage_status": (document.coverage_summary or {}).get("status"),
                    },
                )
            )

        pair_stats = candidate_service.pair_statistics() if hasattr(candidate_service, "pair_statistics") else {}
        document_by_id = {document.id: document for document in ordered}
        for left, right in combinations(ordered, 2):
            pair = tuple(sorted((left.id, right.id)))
            stats = pair_stats.get(pair, {})
            coverage, _ = combine_coverage_summaries(
                (left.coverage_summary, right.coverage_summary)
            )
            db.add(
                DuplicatePairSummary(
                    task_id=task_id,
                    left_document_id=pair[0],
                    right_document_id=pair[1],
                    candidate_count=int(stats.get("candidate_count", 0)),
                    finding_count=0,
                    suspicious_count=0,
                    unknown_count=0,
                    max_evidence_strength=float(stats.get("max_evidence_strength", 0.0)),
                    coverage_status=coverage or default_coverage_status,
                    channel_hits=stats.get("channel_hits")
                    or {"lexical": 0, "structure": 0, "semantic": 0, "image": 0},
                )
            )

        for cluster_data in getattr(candidate_service, "exact_clusters", []):
            cluster_coverage, _ = combine_coverage_summaries(
                document_by_id[document_id].coverage_summary
                for document_id in cluster_data["document_ids"]
                if document_id in document_by_id
            )
            cluster = DuplicateEvidenceCluster(
                task_id=task_id,
                cluster_key=cluster_data["cluster_key"],
                content_type=cluster_data["content_type"],
                document_ids=cluster_data["document_ids"],
                occurrence_count=len(cluster_data["occurrences"]),
                representative_excerpt=(cluster_data["occurrences"][0]["excerpt"] or "")[:2000],
                evidence_strength=1.0,
                coverage_status=cluster_coverage or default_coverage_status,
                cluster_metadata={"hash": cluster_data.get("hash")},
            )
            db.add(cluster)
            await db.flush()
            for occurrence in cluster_data["occurrences"]:
                db.add(
                    DuplicateOccurrence(
                        task_id=task_id,
                        cluster_id=cluster.id,
                        document_id=occurrence["document_id"],
                        block_id=occurrence.get("block_id"),
                        excerpt=occurrence.get("excerpt") or "",
                        location=occurrence.get("location") or {},
                        channel=("image" if cluster_data["content_type"] == "image" else "lexical"),
                    )
                )
        await db.commit()


async def finalize_duplicate_task_matrix(
    session_factory,
    *,
    task_id: str,
    candidate_service,
) -> None:
    """Link findings to clusters and derive pair counters from occurrences."""

    async with session_factory() as db:
        results = list(
            (
                await db.execute(
                    select(DuplicateResult).where(DuplicateResult.task_id == task_id)
                )
            ).scalars().all()
        )
        clusters = list(
            (
                await db.execute(
                    select(DuplicateEvidenceCluster).where(
                        DuplicateEvidenceCluster.task_id == task_id
                    )
                )
            ).scalars().all()
        )
        summaries = list(
            (
                await db.execute(
                    select(DuplicatePairSummary).where(
                        DuplicatePairSummary.task_id == task_id
                    )
                )
            ).scalars().all()
        )
        summary_by_pair = {
            tuple(sorted((summary.left_document_id, summary.right_document_id))): summary
            for summary in summaries
        }
        cluster_data_by_key = {
            cluster["cluster_key"]: cluster
            for cluster in getattr(candidate_service, "exact_clusters", [])
        }
        cluster_rows = {cluster.cluster_key: cluster for cluster in clusters}
        cluster_key_by_block: dict[str, str] = {}
        for key, cluster_data in cluster_data_by_key.items():
            for occurrence in cluster_data["occurrences"]:
                if occurrence.get("block_id"):
                    cluster_key_by_block[occurrence["block_id"]] = key

        counted: set[tuple[str, tuple[str, str]]] = set()
        for result in results:
            evidence = result.evidence or {}
            left_block_id = evidence.get("left_block_id")
            right_block_id = evidence.get("right_block_id")
            left_cluster = cluster_key_by_block.get(left_block_id)
            right_cluster = cluster_key_by_block.get(right_block_id)
            document_pairs: list[tuple[str, str]]
            if left_cluster and left_cluster == right_cluster:
                cluster_row = cluster_rows.get(left_cluster)
                cluster_data = cluster_data_by_key[left_cluster]
                if cluster_row is not None:
                    cluster_row.finding_id = result.id
                    occurrences = list(
                        (
                            await db.execute(
                                select(DuplicateOccurrence).where(
                                    DuplicateOccurrence.cluster_id == cluster_row.id
                                )
                            )
                        ).scalars().all()
                    )
                    for occurrence in occurrences:
                        occurrence.finding_id = result.id
                    # The master writes two direct finding occurrences for
                    # every result.  For an exact multi-document cluster the
                    # seeded cluster occurrences are authoritative; remove
                    # the two overlapping direct rows to avoid double counts.
                    direct_rows = list(
                        (
                            await db.execute(
                                select(DuplicateOccurrence).where(
                                    DuplicateOccurrence.task_id == task_id,
                                    DuplicateOccurrence.finding_id == result.id,
                                    DuplicateOccurrence.cluster_id.is_(None),
                                )
                            )
                        ).scalars().all()
                    )
                    for direct in direct_rows:
                        await db.delete(direct)
                document_pairs = [
                    tuple(sorted(pair))
                    for pair in combinations(sorted(set(cluster_data["document_ids"])), 2)
                ]
            else:
                document_pairs = []
                evidence_occurrences = evidence.get("occurrences")
                if isinstance(evidence_occurrences, list):
                    for occurrence in evidence_occurrences:
                        if not isinstance(occurrence, dict):
                            continue
                        left_document_id = occurrence.get("left_document_id")
                        right_document_id = occurrence.get("right_document_id")
                        if (
                            left_document_id
                            and right_document_id
                            and left_document_id != right_document_id
                        ):
                            document_pairs.append(
                                tuple(sorted((str(left_document_id), str(right_document_id))))
                            )
                if not document_pairs:
                    document_pairs = [
                        tuple(sorted((result.left_document_id, result.right_document_id)))
                    ]
                occurrence_rows = list(
                    (
                        await db.execute(
                            select(DuplicateOccurrence).where(
                                DuplicateOccurrence.task_id == task_id,
                                DuplicateOccurrence.finding_id == result.id,
                            )
                        )
                    ).scalars().all()
                )
                occurrence_document_ids = sorted(
                    {row.document_id for row in occurrence_rows}
                )
                if len(occurrence_document_ids) >= 3:
                    cluster_key = sha256(
                        f"finding\0{result.id}".encode()
                    ).hexdigest()[:32]
                    cluster_row = DuplicateEvidenceCluster(
                        task_id=task_id,
                        finding_id=result.id,
                        cluster_key=cluster_key,
                        content_type=(
                            "image"
                            if result.match_type == "ocr_error"
                            or float((result.channel_scores or {}).get("image_score", 0) or 0) > 0
                            else (
                                "table"
                                if result.match_type == "structural"
                                else "paragraph"
                            )
                        ),
                        document_ids=occurrence_document_ids,
                        occurrence_count=len(occurrence_rows),
                        representative_excerpt=(result.left_excerpt or "")[:2000],
                        evidence_strength=result.confidence,
                        coverage_status=result.coverage_status,
                        cluster_metadata={
                            "match_type": result.match_type,
                            "candidate_ids": evidence.get("candidate_ids") or [],
                            "derived_from_finding": True,
                        },
                    )
                    db.add(cluster_row)
                    await db.flush()
                    for occurrence_row in occurrence_rows:
                        occurrence_row.cluster_id = cluster_row.id
            for pair in document_pairs:
                key = (result.id, pair)
                if key in counted:
                    continue
                counted.add(key)
                summary = summary_by_pair.get(pair)
                if summary is None:
                    continue
                summary.finding_count += 1
                if result.verdict == "suspicious":
                    summary.suspicious_count += 1
                elif result.verdict == "unknown":
                    summary.unknown_count += 1
                if result.confidence is not None:
                    current = float(summary.max_evidence_strength or 0)
                    summary.max_evidence_strength = max(current, float(result.confidence))
        await db.commit()


__all__ = ["finalize_duplicate_task_matrix", "seed_duplicate_task_index"]
