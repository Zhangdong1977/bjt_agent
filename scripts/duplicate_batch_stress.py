"""CLI wrapper for the offline ten-document duplicate stress harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.evaluation.duplicate_batch_stress import run_batch_stress_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the S2 batch duplicate stress harness")
    parser.add_argument("--documents", type=int, default=10)
    parser.add_argument("--rules", type=int, default=4)
    parser.add_argument("--paragraphs", type=int, default=24)
    parser.add_argument("--max-duration-ms", type=int, default=30_000)
    parser.add_argument("--max-memory-mb", type=float, default=512.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_batch_stress_sync(
        document_count=args.documents,
        rule_flow_count=args.rules,
        repeated_paragraphs=args.paragraphs,
    )
    report["gates"] = {
        "duration": report["duration_ms"] <= args.max_duration_ms,
        "memory": report["peak_memory_mb"] <= args.max_memory_mb,
        "single_global_flow": report["independent_llm_flows"] == args.rules,
        "cluster_occurrences": report["shared_cluster_occurrence_count"] == args.documents,
    }
    report["passed"] = all(report["gates"].values())
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
