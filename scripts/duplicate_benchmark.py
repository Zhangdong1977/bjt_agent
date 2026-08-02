"""CLI for replaying the deterministic duplicate candidate benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.evaluation.duplicate_benchmark import run_benchmark_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay duplicate candidate benchmark JSONL")
    parser.add_argument("dataset", type=Path, help="Benchmark JSONL file")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--max-candidates", type=int, default=400)
    args = parser.parse_args()

    report = run_benchmark_sync(
        args.dataset,
        max_candidates=max(1, args.max_candidates),
        top_k=max(1, args.top_k),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
