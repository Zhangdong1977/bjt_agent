"""Offline ten-document stress harness for the S2-3 global index."""

from __future__ import annotations

import asyncio
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

from backend.services.duplicate_batch import MultiDocumentCandidateService
from backend.services.duplicate_candidates import DocumentDescriptor


async def run_batch_stress(
    *,
    document_count: int = 10,
    rule_flow_count: int = 4,
    repeated_paragraphs: int = 24,
    max_candidates: int = 1200,
) -> dict[str, Any]:
    """Build one global index and report scale/flow invariants.

    The harness deliberately avoids invoking an LLM.  ``rule_flow_count`` is
    the number of rule-master flows the production pipeline would run once;
    it must not be multiplied by the document-pair count.
    """

    if not 3 <= document_count <= 10:
        raise ValueError("document_count must be between 3 and 10")
    shared = (
        "本项目采用分区部署、双链路校验、逐项回归和问题闭环机制，"
        "形成可追溯的实施记录与责任边界。"
    )
    with tempfile.TemporaryDirectory(prefix="duplicate-batch-stress-") as temp_dir:
        root = Path(temp_dir)
        descriptors: list[DocumentDescriptor] = []
        for index in range(document_count):
            paragraphs = [
                f"## 共享方案\n\n{shared}",
                *[
                    (
                        f"### 章节 {paragraph_index + 1}\n\n"
                        f"文档 {index + 1} 的实施参数 {1000 + paragraph_index}，"
                        f"响应时间 {20 + index} 分钟，设备型号 X-{index + 1:02d}-{paragraph_index + 1:03d}。"
                    )
                    for paragraph_index in range(repeated_paragraphs)
                ],
            ]
            path = root / f"bid-{index + 1}.md"
            path.write_text("\n\n".join(paragraphs), encoding="utf-8")
            descriptors.append(
                DocumentDescriptor(
                    id=f"document-{index + 1}",
                    filename=path.name,
                    path=str(path),
                    role="duplicate_bid",
                )
            )

        service = MultiDocumentCandidateService(
            descriptors,
            max_candidates=max_candidates,
            semantic_enabled=False,
        )
        tracemalloc.start()
        started = time.perf_counter()
        try:
            await service.build()
            _, peak_bytes = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        duration_ms = int((time.perf_counter() - started) * 1000)
        pair_count = document_count * (document_count - 1) // 2
        shared_cluster = next(
            (
                cluster
                for cluster in service.exact_clusters
                if len(cluster["document_ids"]) == document_count
            ),
            None,
        )
        return {
            "schema_version": "duplicate-batch-stress/v1",
            "algorithm_version": "duplicate-batch/s2-4.1",
            "document_count": document_count,
            "document_pair_count": pair_count,
            "candidate_count": len(service.candidates),
            "exact_cluster_count": len(service.exact_clusters),
            "shared_cluster_occurrence_count": (
                len(shared_cluster["occurrences"]) if shared_cluster else 0
            ),
            "duration_ms": duration_ms,
            "peak_memory_mb": round(peak_bytes / (1024 * 1024), 3),
            "llm_calls": 0,
            "independent_llm_flows": rule_flow_count,
            "pairwise_llm_flow_baseline": pair_count * rule_flow_count,
            "n_squared_llm_flows": False,
            "warnings": service.warnings,
        }


def run_batch_stress_sync(**kwargs) -> dict[str, Any]:
    return asyncio.run(run_batch_stress(**kwargs))


__all__ = ["run_batch_stress", "run_batch_stress_sync"]
