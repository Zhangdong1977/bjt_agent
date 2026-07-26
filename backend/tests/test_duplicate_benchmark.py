"""Tests for the replayable S2-0 duplicate benchmark harness."""

from pathlib import Path

from backend.evaluation.duplicate_benchmark import run_benchmark_sync


def test_phase1_fixture_benchmark_is_reproducible():
    dataset = Path(__file__).parent / "fixtures" / "duplicate" / "benchmark_cases.jsonl"
    report = run_benchmark_sync(dataset, top_k=20)

    assert report["schema_version"] == "duplicate-benchmark/v1"
    assert report["metrics"]["llm_calls"] == 0
    assert report["metrics"]["recall_at_k"] == 1.0
    assert report["case_count"] == 1
