"""Deterministic de-duplication and aggregation for duplicate findings."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Iterable

from backend.schemas.duplicate_check import DuplicateFindingPayload
from backend.services.duplicate_candidates import normalize_text

_VERDICT_PRIORITY = {"reasonable": 1, "unknown": 2, "suspicious": 3}
_CONTIGUOUS_MATCH_TYPES = {"exact", "near_exact", "semantic", "structural"}


def _as_int(value, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _occurrence(finding: DuplicateFindingPayload) -> dict:
    evidence = finding.evidence or {}
    return {
        "left_document_id": evidence.get("left_document_id"),
        "right_document_id": evidence.get("right_document_id"),
        "left_block_id": evidence.get("left_block_id"),
        "right_block_id": evidence.get("right_block_id"),
        "left_excerpt": finding.left_excerpt,
        "left_location": dict(finding.left_location),
        "right_excerpt": finding.right_excerpt,
        "right_location": dict(finding.right_location),
    }


def _occurrences(finding: DuplicateFindingPayload) -> list[dict]:
    evidence = finding.evidence or {}
    stored = evidence.get("occurrences")
    if isinstance(stored, list) and stored:
        return [item for item in stored if isinstance(item, dict)]
    return [_occurrence(finding)]


def _candidate_ids(finding: DuplicateFindingPayload) -> list[str]:
    evidence = finding.evidence or {}
    values = []
    if evidence.get("candidate_id"):
        values.append(str(evidence["candidate_id"]))
    values.extend(str(value) for value in evidence.get("candidate_ids", []) if value)
    return values


def _unique_dicts(items: Iterable[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _finding_priority(finding: DuplicateFindingPayload) -> tuple:
    evidence = finding.evidence or {}
    return (
        _VERDICT_PRIORITY.get(finding.verdict, 0),
        float(evidence.get("evidence_strength") or 0),
        finding.similarity_score,
        min(len(normalize_text(finding.left_excerpt)), len(normalize_text(finding.right_excerpt))),
    )


def collapse_mirrored_findings(
    findings: Iterable[DuplicateFindingPayload],
) -> list[DuplicateFindingPayload]:
    """Fold mirrored/same-content findings and retain all source occurrences."""

    grouped: dict[tuple[str, str, str], list[DuplicateFindingPayload]] = defaultdict(list)
    for finding in findings:
        left = normalize_text(finding.left_excerpt)
        right = normalize_text(finding.right_excerpt)
        pair = tuple(sorted((left, right)))
        key = (normalize_text(finding.check_item_name), pair[0], pair[1])
        grouped[key].append(finding)

    collapsed: list[DuplicateFindingPayload] = []
    for group in grouped.values():
        primary = max(group, key=_finding_priority)
        evidence = dict(primary.evidence or {})
        all_occurrences = _unique_dicts(
            occurrence
            for finding in group
            for occurrence in _occurrences(finding)
        )
        candidate_ids = []
        for finding in group:
            candidate_ids.extend(_candidate_ids(finding))
        evidence.update(
            {
                "occurrences": all_occurrences,
                "collapsed_count": len(all_occurrences),
                "candidate_ids": list(dict.fromkeys(candidate_ids)),
                "evidence_strength": max(
                    float((finding.evidence or {}).get("evidence_strength") or 0)
                    for finding in group
                ),
            }
        )
        collapsed.append(
            primary.model_copy(
                update={
                    "similarity_score": max(finding.similarity_score for finding in group),
                    "evidence": evidence,
                }
            )
        )
    return collapsed


def _same_section(left: dict, right: dict) -> bool:
    return normalize_text(str(left.get("section") or "")) == normalize_text(
        str(right.get("section") or "")
    )


def _is_contiguous(
    previous: DuplicateFindingPayload,
    current: DuplicateFindingPayload,
    *,
    max_line_gap: int,
) -> bool:
    if previous.check_item_name != current.check_item_name:
        return False
    if previous.verdict != current.verdict or previous.source_basis != current.source_basis:
        return False
    if (
        previous.match_type not in _CONTIGUOUS_MATCH_TYPES
        or current.match_type not in _CONTIGUOUS_MATCH_TYPES
    ):
        return False
    if not _same_section(previous.left_location, current.left_location):
        return False
    if not _same_section(previous.right_location, current.right_location):
        return False

    previous_left_end = _as_int(previous.left_location.get("end_line"))
    previous_right_end = _as_int(previous.right_location.get("end_line"))
    current_left_start = _as_int(current.left_location.get("start_line"))
    current_right_start = _as_int(current.right_location.get("start_line"))
    if min(previous_left_end, previous_right_end, current_left_start, current_right_start) < 0:
        return False
    left_gap = current_left_start - previous_left_end
    right_gap = current_right_start - previous_right_end
    return 0 <= left_gap <= max_line_gap and 0 <= right_gap <= max_line_gap


def _merge_location(locations: list[dict]) -> dict:
    merged = dict(locations[0])
    starts = [_as_int(location.get("start_line")) for location in locations]
    ends = [_as_int(location.get("end_line")) for location in locations]
    valid_starts = [value for value in starts if value >= 0]
    valid_ends = [value for value in ends if value >= 0]
    if valid_starts:
        merged["start_line"] = min(valid_starts)
    if valid_ends:
        merged["end_line"] = max(valid_ends)
    merged["aggregated_block_count"] = len(locations)
    return merged


def _join_excerpts(values: Iterable[str], *, max_chars: int = 6000) -> str:
    unique = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    return "\n\n".join(unique)[:max_chars]


def _merge_contiguous_group(
    group: list[DuplicateFindingPayload],
) -> DuplicateFindingPayload:
    if len(group) == 1:
        return group[0]
    primary = max(group, key=_finding_priority)
    evidence = dict(primary.evidence or {})
    occurrences = _unique_dicts(
        occurrence
        for finding in group
        for occurrence in _occurrences(finding)
    )
    candidate_ids = [
        candidate_id
        for finding in group
        for candidate_id in _candidate_ids(finding)
    ]
    evidence.update(
        {
            "occurrences": occurrences,
            "collapsed_count": len(occurrences),
            "aggregated_count": len(group),
            "candidate_ids": list(dict.fromkeys(candidate_ids)),
            "evidence_strength": max(
                float((finding.evidence or {}).get("evidence_strength") or 0)
                for finding in group
            ),
        }
    )
    explanation = primary.explanation
    if len(group) > 1:
        explanation = f"{explanation.rstrip('。')}；已合并同章节连续相似片段 {len(group)} 段。"
    return primary.model_copy(
        update={
            "similarity_score": max(finding.similarity_score for finding in group),
            "left_excerpt": _join_excerpts(finding.left_excerpt for finding in group),
            "left_location": _merge_location([finding.left_location for finding in group]),
            "right_excerpt": _join_excerpts(finding.right_excerpt for finding in group),
            "right_location": _merge_location([finding.right_location for finding in group]),
            "explanation": explanation,
            "evidence": evidence,
        }
    )


def aggregate_contiguous_findings(
    findings: Iterable[DuplicateFindingPayload], *, max_line_gap: int = 3
) -> list[DuplicateFindingPayload]:
    """Merge monotonic neighbouring A/B blocks within the same chapter pair."""

    ordered = sorted(
        findings,
        key=lambda finding: (
            normalize_text(finding.check_item_name),
            normalize_text(str(finding.left_location.get("section") or "")),
            normalize_text(str(finding.right_location.get("section") or "")),
            _as_int(finding.left_location.get("start_line"), 10**9),
            _as_int(finding.right_location.get("start_line"), 10**9),
        ),
    )
    groups: list[list[DuplicateFindingPayload]] = []
    for finding in ordered:
        if groups and _is_contiguous(groups[-1][-1], finding, max_line_gap=max_line_gap):
            groups[-1].append(finding)
        else:
            groups.append([finding])
    return [_merge_contiguous_group(group) for group in groups]


def group_duplicate_findings(
    findings: Iterable[DuplicateFindingPayload], *, max_line_gap: int = 3
) -> list[DuplicateFindingPayload]:
    """Collapse repeated evidence, then aggregate adjacent chapter fragments."""

    return aggregate_contiguous_findings(
        collapse_mirrored_findings(findings), max_line_gap=max_line_gap
    )


# Descriptive aliases for callers/tests that prefer operation-specific names.
deduplicate_duplicate_findings = collapse_mirrored_findings
aggregate_duplicate_findings = group_duplicate_findings


__all__ = [
    "aggregate_contiguous_findings",
    "aggregate_duplicate_findings",
    "collapse_mirrored_findings",
    "deduplicate_duplicate_findings",
    "group_duplicate_findings",
]
