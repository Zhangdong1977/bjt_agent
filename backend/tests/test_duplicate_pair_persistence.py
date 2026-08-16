"""Pair 模式查重任务也必须落库成员/配对汇总（S2-3 矩阵数据源）。

历史缺陷：seed_duplicate_task_index / finalize_duplicate_task_matrix 只在
batch 模式被调用，pair 任务（生产默认模式）两表恒空，矩阵接口只能走合成
兜底。修复后两种模式统一落库；本文件用真实候选服务 + 轻量 DB 替身验证
pair（2 文档 = 1 配对）路径的写入与计数。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services.duplicate_batch import MultiDocumentCandidateService
from backend.services.duplicate_candidates import DocumentDescriptor
from backend.services.duplicate_batch_persistence import (
    finalize_duplicate_task_matrix,
    seed_duplicate_task_index,
)


class _ScalarRows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return _ScalarRows(self._rows)


class _FakeSession:
    """按序返回 execute 结果；add 捕获新增对象。"""

    def __init__(self, results=()):
        self.results = list(results)
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _query):
        if self.results:
            return self.results.pop(0)
        return _Result([])

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True


def _pair_documents(tmp_path: Path) -> list[DocumentDescriptor]:
    shared = "两份投标文件逐字相同的施工组织设计段落，含设备 ZX-9000 与工期 90 天。"
    descriptors = []
    for name, role in (("left.md", "duplicate_left"), ("right.md", "duplicate_right")):
        path = tmp_path / name
        path.write_text(f"# 方案\n\n{shared}\n\n{role} 专属段落。", encoding="utf-8")
        descriptors.append(
            DocumentDescriptor(id=f"doc-{role}", filename=name, path=str(path), role=role)
        )
    return descriptors


@pytest.mark.asyncio
async def test_seed_persists_two_members_and_single_pair_for_pair_mode(tmp_path: Path):
    descriptors = _pair_documents(tmp_path)
    service = MultiDocumentCandidateService(descriptors, semantic_enabled=False)
    await service.build()

    documents = [
        SimpleNamespace(
            id=descriptor.id,
            original_filename=descriptor.filename,
            doc_type=descriptor.role,
            coverage_summary={"status": "ok"},
        )
        for descriptor in descriptors
    ]
    session = _FakeSession()
    await seed_duplicate_task_index(
        lambda: session,
        task_id="task-pair-1",
        documents=documents,
        candidate_service=service,
        default_coverage_status="ok",
    )

    assert session.committed
    members = [item for item in session.added if type(item).__name__ == "DuplicateDocumentMember"]
    pairs = [item for item in session.added if type(item).__name__ == "DuplicatePairSummary"]
    # pair 模式：2 个成员 + 恰好 1 条配对汇总（combinations(2)=1）
    assert len(members) == 2
    assert {member.party_key for member in members} == {"A", "B"}
    assert len(pairs) == 1
    pair = pairs[0]
    assert tuple(sorted((pair.left_document_id, pair.right_document_id))) == tuple(
        sorted((documents[0].id, documents[1].id))
    )
    # 共享段落必然产生 exact 候选 → candidate_count > 0
    assert pair.candidate_count >= 1


@pytest.mark.asyncio
async def test_finalize_increments_pair_counters_for_suspicious_result(tmp_path: Path):
    descriptors = _pair_documents(tmp_path)
    service = MultiDocumentCandidateService(descriptors, semantic_enabled=False)
    await service.build()

    left_id, right_id = descriptors[0].id, descriptors[1].id
    pair_key = tuple(sorted((left_id, right_id)))
    summary = SimpleNamespace(
        left_document_id=pair_key[0],
        right_document_id=pair_key[1],
        finding_count=0,
        suspicious_count=0,
        unknown_count=0,
        max_evidence_strength=0.0,
    )
    result = SimpleNamespace(
        id="finding-1",
        verdict="suspicious",
        confidence=0.94,
        match_type="exact",
        channel_scores={},
        coverage_status="ok",
        left_excerpt="excerpt",
        evidence={},  # 无聚类证据 → 走 result 自身配对回退
        left_document_id=left_id,
        right_document_id=right_id,
    )
    occurrence = SimpleNamespace(document_id=left_id, cluster_id=None)
    session = _FakeSession(results=[_Result([result]), _Result([]), _Result([summary]), _Result([occurrence])])
    await finalize_duplicate_task_matrix(
        lambda: session,
        task_id="task-pair-1",
        candidate_service=service,
    )

    assert session.committed
    assert summary.finding_count == 1
    assert summary.suspicious_count == 1
    assert summary.unknown_count == 0
    assert float(summary.max_evidence_strength) == pytest.approx(0.94)
