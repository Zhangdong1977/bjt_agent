"""Versioned, task-scoped duplicate-check runtime configuration.

The duplicate pipeline has several independently degradable channels.  A task
must use one immutable snapshot of those switches and thresholds for its whole
run; reading ``Settings`` in the middle of a long task would make a result
non-reproducible after an operator changes an environment variable.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.config import Settings


DUPLICATE_ALGORITHM_VERSION = "duplicate-s2-4.2"


def build_duplicate_feature_snapshot(settings: Settings) -> dict[str, Any]:
    """Return a JSON-serialisable snapshot of all duplicate runtime inputs."""

    return {
        "schema_version": "duplicate-runtime/v2",
        "algorithm_version": str(
            getattr(settings, "duplicate_algorithm_version", None)
            or DUPLICATE_ALGORITHM_VERSION
        ),
        "features": {
            "batch": bool(getattr(settings, "duplicate_batch_enabled", False)),
            "semantic": bool(getattr(settings, "duplicate_semantic_enabled", False)),
            "ocr": bool(getattr(settings, "duplicate_ocr_enabled", True)),
            "remote_ocr": bool(getattr(settings, "duplicate_remote_ocr_enabled", False)),
            "vision": bool(getattr(settings, "duplicate_vision_enabled", False)),
        },
        "providers": {
            "semantic": str(getattr(settings, "duplicate_semantic_provider", "llm")),
        },
        "thresholds": {
            "semantic_min_score": float(getattr(settings, "duplicate_semantic_min_score", 0.72)),
            "candidate_min_score": float(getattr(settings, "duplicate_candidate_min_score", 0.45)),
            "lexical_min_score": float(getattr(settings, "duplicate_lexical_min_score", 0.16)),
            "structure_min_score": float(getattr(settings, "duplicate_structure_min_score", 0.50)),
            "near_exact_min_score": float(getattr(settings, "duplicate_near_exact_min_score", 0.72)),
            "image_min_score": float(getattr(settings, "duplicate_image_min_score", 0.78)),
            "ocr_min_local_confidence": float(
                getattr(settings, "duplicate_ocr_min_local_confidence", 0.72)
            ),
            "scan_text_threshold": int(
                getattr(settings, "duplicate_scan_text_threshold", 30)
            ),
        },
        "budgets": {
            "embedding_batch_size": int(getattr(settings, "duplicate_embedding_batch_size", 32)),
            "embedding_max_blocks": int(getattr(settings, "duplicate_embedding_max_blocks", 400)),
            "embedding_max_input_chars": int(getattr(settings, "duplicate_embedding_max_input_chars", 500_000)),
            "embedding_min_chars": int(getattr(settings, "duplicate_embedding_min_chars", 24)),
            "ocr_max_images": int(getattr(settings, "duplicate_ocr_max_images", 24)),
            "remote_ocr_max_calls": int(getattr(settings, "duplicate_remote_ocr_max_calls", 4)),
            "vision_max_calls": int(getattr(settings, "duplicate_vision_max_calls", 2)),
            "pair_max_candidates": int(getattr(settings, "duplicate_pair_max_candidates", 400)),
            "batch_max_candidates": int(getattr(settings, "duplicate_batch_max_candidates", 1200)),
            "embedding_timeout_seconds": float(
                getattr(settings, "duplicate_embedding_timeout_seconds", 45.0)
            ),
            "agent_total_timeout_seconds": int(
                getattr(settings, "agent_total_timeout", 5400)
            ),
        },
    }


def snapshot_for_task(task: Any, settings: Settings) -> dict[str, Any]:
    """Read a persisted snapshot, backfilling legacy tasks once if needed."""

    existing = getattr(task, "duplicate_feature_snapshot", None)
    if isinstance(existing, dict) and existing.get("schema_version"):
        return deepcopy(existing)
    snapshot = build_duplicate_feature_snapshot(settings)
    # ``task`` may be a light-weight test double, hence the defensive setattr.
    try:
        task.duplicate_feature_snapshot = snapshot
        task.duplicate_algorithm_version = snapshot["algorithm_version"]
    except Exception:
        pass
    return snapshot


def feature(snapshot: dict[str, Any], name: str, default: bool = False) -> bool:
    return bool((snapshot.get("features") or {}).get(name, default))


def threshold(snapshot: dict[str, Any], name: str, default: float) -> float:
    value = (snapshot.get("thresholds") or {}).get(name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def budget(snapshot: dict[str, Any], name: str, default: int) -> int:
    value = (snapshot.get("budgets") or {}).get(name, default)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default))


def decimal_budget(snapshot: dict[str, Any], name: str, default: float) -> float:
    value = (snapshot.get("budgets") or {}).get(name, default)
    try:
        return max(0.1, float(value))
    except (TypeError, ValueError):
        return max(0.1, float(default))


def provider(snapshot: dict[str, Any], name: str, default: str) -> str:
    value = (snapshot.get("providers") or {}).get(name, default)
    return str(value or default)


__all__ = [
    "DUPLICATE_ALGORITHM_VERSION",
    "build_duplicate_feature_snapshot",
    "snapshot_for_task",
    "feature",
    "threshold",
    "budget",
    "decimal_budget",
    "provider",
]
