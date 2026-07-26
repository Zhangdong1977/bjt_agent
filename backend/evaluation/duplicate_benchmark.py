"""Replayable benchmark harness for duplicate candidate retrieval.

The JSONL dataset intentionally stores only file references and short expected
substrings.  Real evaluation sets can therefore be de-identified and kept out
of production databases while still producing version-comparable metrics.
"""

from __future__ import annotations

import asyncio
import json
import time
import tracemalloc
from pathlib import Path
from typing import Any

from backend.services.duplicate_candidates import (
    DocumentDescriptor,
    DuplicateCandidate,
    DuplicateCandidateService,
)

BENCHMARK_SCHEMA_VERSION = "duplicate-benchmark/v1"
ALGORITHM_VERSION = "duplicate-candidates/s2-4.1"


def _case_category(case: dict[str, Any]) -> str:
    value = str(case.get("category") or "text").strip().lower()
    return value if value in {"text", "table", "semantic", "image", "source", "batch"} else "text"


def load_cases(dataset_path: str | Path) -> list[dict[str, Any]]:
    path = Path(dataset_path)
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid benchmark JSON at line {line_number}: {exc}") from exc
        if not isinstance(case, dict) or not case.get("case_id"):
            raise ValueError(f"Benchmark line {line_number} must contain case_id")
        cases.append(case)
    return cases


def _resolve_path(dataset_path: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (dataset_path.parent / path).resolve()


def _candidate_matches(candidate: DuplicateCandidate, expected: dict[str, Any]) -> bool:
    left_text = str(expected.get("left_contains") or "").strip()
    right_text = str(expected.get("right_contains") or "").strip()
    if not left_text or not right_text:
        return False
    return left_text in candidate.left.text and right_text in candidate.right.text


async def run_case(
    case: dict[str, Any],
    *,
    dataset_path: Path,
    max_candidates: int = 400,
    top_k: int = 20,
) -> dict[str, Any]:
    left_path = _resolve_path(dataset_path, str(case["left_path"]))
    right_path = _resolve_path(dataset_path, str(case["right_path"]))
    if not left_path.is_file() or not right_path.is_file():
        raise FileNotFoundError(
            f"Benchmark case {case['case_id']} source missing: {left_path}, {right_path}"
        )

    service = DuplicateCandidateService(
        DocumentDescriptor(id=f"{case['case_id']}-left", filename=left_path.name, path=str(left_path)),
        DocumentDescriptor(id=f"{case['case_id']}-right", filename=right_path.name, path=str(right_path)),
        max_candidates=max_candidates,
    )
    started = time.perf_counter()
    await service.build()
    duration_ms = int((time.perf_counter() - started) * 1000)
    candidates = service.candidates[: max(1, top_k)]
    expected_pairs = list(case.get("expected_pairs") or [])
    expected_hits = [
        any(_candidate_matches(candidate, expected) for candidate in candidates)
        for expected in expected_pairs
    ]
    candidate_hits = [
        any(_candidate_matches(candidate, expected) for expected in expected_pairs)
        for candidate in candidates
    ]
    recall = sum(expected_hits) / len(expected_hits) if expected_hits else 1.0
    precision = sum(candidate_hits) / len(candidates) if candidates else (1.0 if not expected_pairs else 0.0)
    return {
        "case_id": case["case_id"],
        "category": _case_category(case),
        "candidate_count": len(service.candidates),
        "evaluated_candidate_count": len(candidates),
        "expected_pair_count": len(expected_pairs),
        "matched_expected_count": sum(expected_hits),
        "recall_at_k": round(recall, 6),
        "precision_at_k": round(precision, 6),
        "duration_ms": duration_ms,
        "top_candidates": [
            {
                "candidate_id": candidate.id,
                "similarity_score": round(candidate.similarity_score, 6),
                "evidence_strength": round(candidate.evidence_strength, 6),
                "match_type": candidate.match_type,
                "left_excerpt": candidate.left.text[:200],
                "right_excerpt": candidate.right.text[:200],
            }
            for candidate in candidates
        ],
        "channel_counts": {
            "text": sum(
                candidate.left.content_type in {"paragraph", "heading", "caption"}
                and candidate.right.content_type in {"paragraph", "heading", "caption"}
                for candidate in candidates
            ),
            "table": sum(
                candidate.left.content_type in {"table", "table_row"}
                or candidate.right.content_type in {"table", "table_row"}
                for candidate in candidates
            ),
            "image": sum(
                candidate.left.content_type in {"image", "image_ocr"}
                or candidate.right.content_type in {"image", "image_ocr"}
                for candidate in candidates
            ),
            "semantic": sum(candidate.match_type == "semantic" for candidate in candidates),
        },
    }


async def run_benchmark(
    dataset_path: str | Path,
    *,
    max_candidates: int = 400,
    top_k: int = 20,
) -> dict[str, Any]:
    path = Path(dataset_path).resolve()
    cases = load_cases(path)
    tracemalloc.start()
    started = time.perf_counter()
    try:
        results = [
            await run_case(
                case,
                dataset_path=path,
                max_candidates=max_candidates,
                top_k=top_k,
            )
            for case in cases
        ]
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    expected_total = sum(item["expected_pair_count"] for item in results)
    matched_total = sum(item["matched_expected_count"] for item in results)
    evaluated_total = sum(item["evaluated_candidate_count"] for item in results)
    candidate_hit_estimate = sum(
        round(item["precision_at_k"] * item["evaluated_candidate_count"])
        for item in results
    )
    category_metrics: dict[str, dict[str, Any]] = {}
    for category in sorted({item["category"] for item in results}):
        subset = [item for item in results if item["category"] == category]
        expected = sum(item["expected_pair_count"] for item in subset)
        matched = sum(item["matched_expected_count"] for item in subset)
        evaluated = sum(item["evaluated_candidate_count"] for item in subset)
        hits = sum(
            round(item["precision_at_k"] * item["evaluated_candidate_count"])
            for item in subset
        )
        category_metrics[category] = {
            "case_count": len(subset),
            "recall_at_k": round(matched / expected, 6) if expected else 1.0,
            "precision_at_k": round(hits / evaluated, 6) if evaluated else (1.0 if not expected else 0.0),
            "candidate_count": sum(item["candidate_count"] for item in subset),
            "channel_counts": {
                channel: sum(item["channel_counts"].get(channel, 0) for item in subset)
                for channel in ("text", "table", "semantic", "image")
            },
        }
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "dataset": path.name,
        "case_count": len(results),
        "top_k": top_k,
        "max_candidates": max_candidates,
        "metrics": {
            "recall_at_k": round(matched_total / expected_total, 6) if expected_total else 1.0,
            "precision_at_k": (
                round(candidate_hit_estimate / evaluated_total, 6)
                if evaluated_total
                else (1.0 if not expected_total else 0.0)
            ),
            "candidate_count": sum(item["candidate_count"] for item in results),
            "duration_ms": sum(item["duration_ms"] for item in results),
            "llm_calls": 0,
            "estimated_cost_yuan": 0.0,
            "wall_time_ms": int((time.perf_counter() - started) * 1000),
            "peak_memory_mb": round(peak_bytes / (1024 * 1024), 3),
            "unknown_rate": 0.0,
        },
        "category_metrics": category_metrics,
        "cases": results,
    }


def run_benchmark_sync(
    dataset_path: str | Path,
    *,
    max_candidates: int = 400,
    top_k: int = 20,
) -> dict[str, Any]:
    # Avoid ``asyncio.run`` here: it clears the process-wide current loop on
    # Python 3.11+, while parts of this legacy test suite still call
    # ``asyncio.get_event_loop().run_until_complete``.  Preserve or restore a
    # usable loop so an offline benchmark does not affect unrelated callers.
    try:
        previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            run_benchmark(dataset_path, max_candidates=max_candidates, top_k=top_k)
        )
    finally:
        loop.close()
        if previous_loop is not None and not previous_loop.is_closed():
            asyncio.set_event_loop(previous_loop)
        else:
            asyncio.set_event_loop(asyncio.new_event_loop())


__all__ = [
    "ALGORITHM_VERSION",
    "BENCHMARK_SCHEMA_VERSION",
    "load_cases",
    "run_benchmark",
    "run_benchmark_sync",
    "run_case",
]
