"""S2-2B batch embedding, cache, breaker and hybrid-recall tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services.duplicate_candidates import DocumentDescriptor, DuplicateCandidateService
from backend.services.embedding_service import EmbeddingService, cosine_similarity


class _FakeEmbeddings:
    def __init__(self, vectors: dict[str, list[float]], calls: list[list[str]], fail=False):
        self.vectors = vectors
        self.calls = calls
        self.fail = fail

    async def create(self, *, model, input):
        values = [input] if isinstance(input, str) else list(input)
        self.calls.append(values)
        if self.fail:
            raise RuntimeError("provider unavailable")
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=self.vectors[value])
                for index, value in enumerate(values)
            ]
        )


class _FakeClient:
    def __init__(self, vectors, calls, fail=False):
        self.embeddings = _FakeEmbeddings(vectors, calls, fail=fail)


@pytest.mark.asyncio
async def test_batch_deduplicates_and_cache_avoids_second_external_call(tmp_path: Path):
    calls: list[list[str]] = []
    client = _FakeClient({"同一段技术文本": [1.0, 0.0]}, calls)
    service = EmbeddingService(
        client=client,
        cache_dir=tmp_path / "cache",
        batch_size=8,
    )
    first = await service.embed_batch(["同一段技术文本", "同一段技术文本"])
    assert first == [[1.0, 0.0], [1.0, 0.0]]
    assert calls == [["同一段技术文本"]]
    assert service.last_stats.external_inputs == 1

    second = await service.embed_batch(["同一段技术文本"])
    assert second == [[1.0, 0.0]]
    assert len(calls) == 1
    assert service.last_stats.cache_hits == 1
    assert service.last_stats.external_inputs == 0


@pytest.mark.asyncio
async def test_embedding_budget_skips_excess_input_without_failure(tmp_path: Path):
    calls: list[list[str]] = []
    client = _FakeClient({"12345": [1.0], "67890": [0.0]}, calls)
    service = EmbeddingService(
        client=client,
        cache_dir=tmp_path / "cache",
        max_input_chars=5,
    )
    vectors = await service.embed_batch(["12345", "67890"])
    assert vectors[0] == [1.0]
    assert vectors[1] is None
    assert service.last_stats.skipped_inputs == 1
    assert calls == [["12345"]]


@pytest.mark.asyncio
async def test_circuit_breaker_returns_none_and_deterministic_fallback_can_continue(tmp_path: Path):
    calls: list[list[str]] = []
    service = EmbeddingService(
        client=_FakeClient({}, calls, fail=True),
        cache_dir=tmp_path / "cache",
        batch_size=1,
        breaker_failures=2,
        breaker_cooldown_seconds=600,
    )
    vectors = await service.embed_batch(["第一段较长文本", "第二段较长文本"])
    assert vectors == [None, None]
    assert service.breaker_open is True
    later = await service.embed_batch(["第三段文本"])
    assert later == [None]
    assert service.last_stats.degraded_reason == "embedding_circuit_open"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_semantic_channel_recalls_paraphrase_with_low_lexical_overlap(tmp_path: Path):
    left_text = "供应商须建立全天候故障响应机制，并在接报后迅速组织现场处置。"
    right_text = "投标人承诺任何时段收到异常通知后立即派遣工程师前往解决问题。"
    unrelated = "本项目设备包装应采用防潮木箱并标注运输方向。"
    calls: list[list[str]] = []
    vectors = {
        left_text: [1.0, 0.0, 0.0],
        right_text: [0.98, 0.02, 0.0],
        unrelated: [0.0, 1.0, 0.0],
    }
    embedding = EmbeddingService(
        client=_FakeClient(vectors, calls),
        cache_dir=tmp_path / "cache",
    )
    left = tmp_path / "left.md"
    right = tmp_path / "right.md"
    left.write_text(left_text, encoding="utf-8")
    right.write_text(f"{right_text}\n\n{unrelated}", encoding="utf-8")
    service = DuplicateCandidateService(
        DocumentDescriptor(id="left", filename="A.md", path=str(left)),
        DocumentDescriptor(id="right", filename="B.md", path=str(right)),
        embedding_service=embedding,
        semantic_enabled=True,
        semantic_min_score=0.80,
    )
    candidates = await service.build()
    semantic = next(
        candidate
        for candidate in candidates
        if candidate.left.text == left_text and candidate.right.text == right_text
    )
    assert semantic.match_type == "semantic"
    assert semantic.semantic_score > 0.99
    assert semantic.to_agent_dict()["semantic_score"] > 0.99


@pytest.mark.asyncio
async def test_semantic_flag_off_makes_zero_embedding_calls(tmp_path: Path):
    calls: list[list[str]] = []
    text = "这是足够长的技术方案正文，用于确认关闭语义开关后不会调用外部向量服务。"
    client = _FakeClient({text: [1.0]}, calls)
    embedding = EmbeddingService(client=client, cache_dir=tmp_path / "cache")
    left = tmp_path / "left.md"
    right = tmp_path / "right.md"
    left.write_text(text, encoding="utf-8")
    right.write_text(text, encoding="utf-8")
    service = DuplicateCandidateService(
        DocumentDescriptor(id="left", filename="A.md", path=str(left)),
        DocumentDescriptor(id="right", filename="B.md", path=str(right)),
        embedding_service=embedding,
        semantic_enabled=False,
    )
    await service.build()
    assert calls == []
    assert service.candidates[0].match_type == "exact"


def test_cosine_similarity_is_bounded_and_handles_missing_vectors():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity(None, [1.0]) == 0.0


def test_embedding_usage_migration_and_summary_fields_exist():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations" / "028_add_embedding_usage.sql").read_text(
        encoding="utf-8"
    )
    summary = (root / "services" / "usage_summary.py").read_text(encoding="utf-8")
    assert "embedding_cache_hits" in migration
    assert "vision_calls" in migration
    assert "usage_type = 'embedding'" in summary
    assert "embedding_input_tokens" in summary
