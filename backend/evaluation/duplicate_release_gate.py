"""Evaluate a classified S2 benchmark report against release thresholds."""

from __future__ import annotations

from typing import Any


DEFAULT_CATEGORY_GATES = {
    "text": {"recall_at_k": 0.90},
    "table": {"recall_at_k": 0.90},
    "semantic": {"recall_at_k": 0.85},
    "image": {"recall_at_k": 0.90},
    "source": {"recall_at_k": 1.00},
}


def evaluate_release_gate(
    report: dict[str, Any],
    *,
    category_gates: dict[str, dict[str, float]] | None = None,
    minimum_precision: float = 0.75,
    maximum_peak_memory_mb: float = 512.0,
) -> dict[str, Any]:
    gates = category_gates or DEFAULT_CATEGORY_GATES
    category_metrics = report.get("category_metrics") or {}
    checks: list[dict[str, Any]] = []
    for category, thresholds in gates.items():
        metrics = category_metrics.get(category)
        if not isinstance(metrics, dict):
            checks.append(
                {
                    "name": f"category:{category}",
                    "passed": False,
                    "reason": "classified benchmark data missing",
                }
            )
            continue
        for metric_name, minimum in thresholds.items():
            actual = float(metrics.get(metric_name) or 0.0)
            checks.append(
                {
                    "name": f"{category}:{metric_name}",
                    "passed": actual >= minimum,
                    "actual": actual,
                    "minimum": minimum,
                }
            )

    overall = report.get("metrics") or {}
    precision = float(overall.get("precision_at_k") or 0.0)
    peak_memory = float(overall.get("peak_memory_mb") or 0.0)
    checks.extend(
        [
            {
                "name": "overall:precision_at_k",
                "passed": precision >= minimum_precision,
                "actual": precision,
                "minimum": minimum_precision,
            },
            {
                "name": "overall:peak_memory_mb",
                "passed": peak_memory <= maximum_peak_memory_mb,
                "actual": peak_memory,
                "maximum": maximum_peak_memory_mb,
            },
            {
                "name": "overall:no_llm_in_candidate_benchmark",
                "passed": int(overall.get("llm_calls") or 0) == 0,
                "actual": int(overall.get("llm_calls") or 0),
                "maximum": 0,
            },
        ]
    )
    if "source_violation_count" in overall:
        checks.append(
            {
                "name": "overall:source_violation_count",
                "passed": int(overall["source_violation_count"]) == 0,
                "actual": int(overall["source_violation_count"]),
                "maximum": 0,
            }
        )
    else:
        checks.append(
            {
                "name": "overall:source_violation_count",
                "passed": False,
                "reason": "verdict/source annotation report missing",
            }
        )
    if "overaggregation_rate" in overall:
        checks.append(
            {
                "name": "overall:overaggregation_rate",
                "passed": float(overall["overaggregation_rate"]) < 0.02,
                "actual": float(overall["overaggregation_rate"]),
                "maximum_exclusive": 0.02,
            }
        )
    else:
        checks.append(
            {
                "name": "overall:overaggregation_rate",
                "passed": False,
                "reason": "aggregation annotation report missing",
            }
        )
    return {
        "schema_version": "duplicate-release-gate/v1",
        "algorithm_version": report.get("algorithm_version"),
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "external_validation_required": [
            "pre-release internal account acceptance",
            "pre-release external account acceptance",
            "real PDF/DOCX/scanned-PDF samples",
            "production release approval",
        ],
    }


__all__ = ["DEFAULT_CATEGORY_GATES", "evaluate_release_gate"]
