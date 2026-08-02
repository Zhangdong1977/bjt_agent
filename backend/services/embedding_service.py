"""Batched, cached and fail-safe embedding service.

Embeddings supplement candidate recall only.  Callers receive ``None`` for a
failed/budget-excluded input and can continue with deterministic channels.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from openai import AsyncOpenAI

from backend.config import get_settings
from backend.services.document_artifacts import normalize_text

logger = logging.getLogger(__name__)

SEVERITY_ORDER: dict[str, int] = {"critical": 3, "major": 2, "minor": 1}


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    magnitude_left = math.sqrt(sum(value * value for value in left))
    magnitude_right = math.sqrt(sum(value * value for value in right))
    if magnitude_left == 0 or magnitude_right == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (magnitude_left * magnitude_right)))


@dataclass(slots=True)
class EmbeddingBatchStats:
    requested_inputs: int = 0
    unique_inputs: int = 0
    external_inputs: int = 0
    cache_hits: int = 0
    skipped_inputs: int = 0
    failed_batches: int = 0
    input_chars: int = 0
    latency_ms: int = 0
    degraded_reason: str | None = None
    provider: str | None = None
    model: str | None = None
    mode: str | None = None


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


class EmbeddingService:
    """Provider-neutral batch embeddings with normalized-hash disk cache."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        cache_dir: Path | None = None,
        enabled: bool = True,
        provider_mode: str | None = None,
        llm_client: Any | None = None,
        batch_size: int | None = None,
        timeout_seconds: float | None = None,
        max_input_chars: int | None = None,
        breaker_failures: int | None = None,
        breaker_cooldown_seconds: int | None = None,
    ):
        settings = get_settings()
        self.batch_size = max(1, int(batch_size or settings.duplicate_embedding_batch_size))
        self.timeout_seconds = max(
            1.0,
            float(timeout_seconds or settings.duplicate_embedding_timeout_seconds),
        )
        self.max_input_chars = max(
            1,
            int(max_input_chars or settings.duplicate_embedding_max_input_chars),
        )
        self.breaker_failures = max(
            1,
            int(breaker_failures or settings.duplicate_embedding_breaker_failures),
        )
        self.breaker_cooldown_seconds = max(
            1,
            int(
                breaker_cooldown_seconds
                or settings.duplicate_embedding_breaker_cooldown_seconds
            ),
        )
        configured_mode = str(
            provider_mode or getattr(settings, "duplicate_semantic_provider", "llm")
        ).lower()
        self.mode = "embedding_api" if client is not None else configured_mode
        self.llm_client = llm_client
        self.enabled = bool(enabled)
        self._initialization_error: str | None = None

        if self.mode == "llm":
            self.provider = f"{settings.llm_provider}_semantic_cluster"
            self.model = {
                "deepseek": settings.deepseek_model,
                "volcengine": settings.volcengine_model,
                "minimax": settings.mini_agent_model,
            }.get(settings.llm_provider, settings.llm_provider)
            self.client = None
            if self.enabled and self.llm_client is None:
                try:
                    from backend.services.llm_factory import create_llm_client

                    self.llm_client = create_llm_client(timeout=self.timeout_seconds)
                except Exception as exc:
                    self.enabled = False
                    self._initialization_error = (
                        f"semantic_llm_unconfigured:{type(exc).__name__}"
                    )
        elif configured_mode == "volcengine":
            base_url = settings.volcengine_api_base.rstrip("/")
            api_key = settings.volcengine_api_key
            self.model = settings.volcengine_embedding_model
            self.provider = "volcengine_embedding"
            self.client = client
        elif configured_mode == "minimax" or client is not None:
            base_url = settings.mini_agent_api_base.rstrip("/")
            if not base_url.endswith("/v1"):
                base_url += "/v1"
            api_key = settings.mini_agent_api_key
            self.model = settings.minimax_embedding_model
            self.provider = "minimax_embedding"
            self.client = client
        else:
            raise ValueError(f"unsupported duplicate semantic provider: {configured_mode}")

        if self.mode != "llm" and client is None and self.enabled:
            try:
                self.client = AsyncOpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=self.timeout_seconds,
                    max_retries=1,
                )
            except Exception as exc:
                self.client = None
                self.enabled = False
                self._initialization_error = f"embedding_unconfigured:{type(exc).__name__}"
        elif self.mode != "llm" and not self.enabled:
            self.client = None
        self.cache_dir = cache_dir or (
            settings.workspace_path / ".duplicate_cache" / "embeddings"
        )
        self._memory_cache: dict[str, list[float]] = {}
        self._task_pair_scores: dict[frozenset[str], float] = {}
        self._consecutive_failures = 0
        self._breaker_opened_at: float | None = None
        self.last_stats = EmbeddingBatchStats()

    @property
    def breaker_open(self) -> bool:
        if self._breaker_opened_at is None:
            return False
        if time.monotonic() - self._breaker_opened_at >= self.breaker_cooldown_seconds:
            self._breaker_opened_at = None
            self._consecutive_failures = 0
            return False
        return True

    def _key(self, normalized: str) -> str:
        return sha256(
            f"embedding/v2\0{self.provider}\0{self.model}\0{normalized}".encode("utf-8")
        ).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / self.provider / self.model.replace("/", "_") / key[:2] / f"{key}.json"

    def _load_cache(self, key: str) -> list[float] | None:
        if key in self._memory_cache:
            return self._memory_cache[key]
        path = self._cache_path(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            vector = [float(value) for value in payload["embedding"]]
            self._memory_cache[key] = vector
            return vector
        except Exception:
            logger.warning("Invalid embedding cache ignored: %s", path, exc_info=True)
            return None

    def _save_cache(self, key: str, vector: list[float]) -> None:
        self._memory_cache[key] = vector
        try:
            _atomic_json(
                self._cache_path(key),
                {
                    "schema_version": "duplicate-embedding-cache/v2",
                    "provider": self.provider,
                    "model": self.model,
                    "embedding": vector,
                },
            )
        except Exception:
            logger.warning("Unable to write embedding cache", exc_info=True)

    async def embed_batch(self, texts: Iterable[str]) -> list[list[float] | None]:
        requested = list(texts)
        stats = EmbeddingBatchStats(
            requested_inputs=len(requested),
            provider=self.provider,
            model=self.model,
            mode=self.mode,
        )
        self.last_stats = stats
        if not requested:
            return []
        if not self.enabled:
            stats.skipped_inputs = len(requested)
            stats.degraded_reason = self._initialization_error or "embedding_disabled"
            return [None] * len(requested)
        if self.breaker_open:
            stats.skipped_inputs = len(requested)
            stats.degraded_reason = "embedding_circuit_open"
            return [None] * len(requested)

        normalized = [normalize_text(text) for text in requested]
        unique: dict[str, str] = {}
        keys: list[str | None] = []
        consumed_chars = 0
        for raw_text, value in zip(requested, normalized):
            if not value:
                keys.append(None)
                stats.skipped_inputs += 1
                continue
            key = self._key(value)
            keys.append(key)
            if key not in unique:
                if consumed_chars + len(value) > self.max_input_chars:
                    stats.skipped_inputs += 1
                    continue
                # Use the original text for provider quality while the cache
                # key remains based on normalized text.
                unique[key] = str(raw_text)
                consumed_chars += len(value)
        stats.unique_inputs = len(unique)
        stats.input_chars = consumed_chars

        if self.mode == "llm":
            return await self._embed_with_llm_clusters(
                requested=requested,
                unique=unique,
                keys=keys,
                stats=stats,
            )

        vectors: dict[str, list[float]] = {}
        missing: list[tuple[str, str]] = []
        for key, value in unique.items():
            cached = self._load_cache(key)
            if cached is not None:
                vectors[key] = cached
                stats.cache_hits += 1
            else:
                missing.append((key, value))
        # Charge/audit only provider-bound input; cached text is represented by
        # cache_hits and must not be billed again.
        stats.input_chars = sum(len(value) for _, value in missing)

        started = time.perf_counter()
        for offset in range(0, len(missing), self.batch_size):
            batch = missing[offset : offset + self.batch_size]
            try:
                response = await asyncio.wait_for(
                    self.client.embeddings.create(  # type: ignore[union-attr]
                        model=self.model,
                        input=[value for _, value in batch],
                    ),
                    timeout=self.timeout_seconds,
                )
                returned = sorted(response.data, key=lambda item: getattr(item, "index", 0))
                if len(returned) != len(batch):
                    raise ValueError("embedding provider returned unexpected vector count")
                for (key, _), item in zip(batch, returned):
                    vector = [float(value) for value in item.embedding]
                    vectors[key] = vector
                    self._save_cache(key, vector)
                stats.external_inputs += len(batch)
                self._consecutive_failures = 0
            except Exception as exc:
                stats.failed_batches += 1
                self._consecutive_failures += 1
                logger.warning("Embedding batch failed; deterministic fallback remains active: %s", exc)
                if self._consecutive_failures >= self.breaker_failures:
                    self._breaker_opened_at = time.monotonic()
                    stats.degraded_reason = "embedding_circuit_open"
                    break
        stats.latency_ms = int((time.perf_counter() - started) * 1000)
        if stats.failed_batches and stats.degraded_reason is None:
            stats.degraded_reason = "embedding_partial_failure"
        self._record_usage(stats)
        return [vectors.get(key) if key is not None else None for key in keys]

    async def _embed_with_llm_clusters(
        self,
        *,
        requested: list[str],
        unique: dict[str, str],
        keys: list[str | None],
        stats: EmbeddingBatchStats,
    ) -> list[list[float] | None]:
        """Create task-local one-hot vectors from strict LLM semantic clusters.

        Dedicated embedding endpoints are not universally provisioned in all
        deployments.  This bounded mode makes one auditable LLM call for the
        task's eligible blocks, asks for conservative equivalence clusters,
        and converts those cluster ids into vectors.  It remains a recall-only
        channel; rule agents still decide whether a candidate is suspicious.
        """

        items = list(unique.items())
        if not items:
            return [None] * len(requested)
        payload = [
            {"id": index, "text": text}
            for index, (_key, text) in enumerate(items)
        ]
        prompt = (
            "找出下列片段中语义相似度不低于0.72的片段对。重点识别同义替换、语序调整、"
            "主动被动转换、否定式反说，以及对象/动作/条件/结果一一对应但表述不同的改写；"
            "不要因字面重合低而漏掉。仅共享行业通用词、数字或标题但核心含义不同的不能匹配。"
            "只输出 JSON 对象，格式为 {\"pairs\":[{\"left_id\":0,\"right_id\":1,"
            "\"score\":0.86}]}；无匹配时 pairs 为空。\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        started = time.perf_counter()
        response = None
        try:
            from backend.utils.mini_agent_utils import setup_mini_agent_path

            setup_mini_agent_path()
            from mini_agent.schema import Message

            response = await asyncio.wait_for(
                self.llm_client.generate(  # type: ignore[union-attr]
                    messages=[
                        Message(
                            role="system",
                            content=(
                                "你是标书语义相似候选召回器。输出可复核的高召回候选对，不做违规结论。"
                                "不得输出解释或 Markdown。"
                            ),
                        ),
                        Message(role="user", content=prompt),
                    ]
                ),
                timeout=self.timeout_seconds,
            )
            pairs = self._parse_llm_pairs(response.content, len(items))
            parents = list(range(len(items)))

            def find(index: int) -> int:
                while parents[index] != index:
                    parents[index] = parents[parents[index]]
                    index = parents[index]
                return index

            def union(left_id: int, right_id: int) -> None:
                left_root, right_root = find(left_id), find(right_id)
                if left_root != right_root:
                    parents[right_root] = left_root

            self._task_pair_scores = {}
            for left_id, right_id, score in pairs:
                union(left_id, right_id)
                pair_key = frozenset((items[left_id][0], items[right_id][0]))
                self._task_pair_scores[pair_key] = max(
                    score,
                    self._task_pair_scores.get(pair_key, 0.0),
                )
            roots = [find(index) for index in range(len(items))]
            cluster_order = list(dict.fromkeys(roots))
            cluster_index = {root: index for index, root in enumerate(cluster_order)}
            vectors: dict[str, list[float]] = {}
            for (key, _text), root in zip(items, roots):
                vector = [0.0] * len(cluster_order)
                vector[cluster_index[root]] = 1.0
                vectors[key] = vector
            stats.external_inputs = len(items)
            stats.input_chars = sum(len(text) for _key, text in items)
            stats.latency_ms = int((time.perf_counter() - started) * 1000)
            self._consecutive_failures = 0
            self._record_llm_usage(response=response, stats=stats, status="success")
            return [vectors.get(key) if key is not None else None for key in keys]
        except Exception as exc:
            stats.latency_ms = int((time.perf_counter() - started) * 1000)
            stats.failed_batches = 1
            stats.degraded_reason = "semantic_llm_failure"
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.breaker_failures:
                self._breaker_opened_at = time.monotonic()
                stats.degraded_reason = "embedding_circuit_open"
            logger.warning(
                "LLM semantic clustering failed; deterministic fallback remains active: %s",
                exc,
            )
            self._record_llm_usage(
                response=response,
                stats=stats,
                status="error",
                error_message=str(exc),
            )
            return [None] * len(requested)
        finally:
            if stats.latency_ms <= 0:
                stats.latency_ms = int((time.perf_counter() - started) * 1000)

    @staticmethod
    def _parse_llm_pairs(
        content: str,
        expected_count: int,
    ) -> list[tuple[int, int, float]]:
        value = str(content or "").strip()
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end < start:
            raise ValueError("semantic pair response is not a JSON object")
        payload = json.loads(value[start : end + 1])
        items = payload.get("pairs") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("semantic pair response has no pairs array")
        pairs: dict[tuple[int, int], float] = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("semantic pair item is not an object")
            left_id = int(item.get("left_id"))
            right_id = int(item.get("right_id"))
            score = float(item.get("score"))
            if (
                left_id < 0
                or right_id < 0
                or left_id >= expected_count
                or right_id >= expected_count
                or left_id == right_id
                or not 0.0 <= score <= 1.0
            ):
                raise ValueError("semantic pair response contains invalid values")
            if score < 0.72:
                continue
            key = tuple(sorted((left_id, right_id)))
            pairs[key] = max(score, pairs.get(key, 0.0))
        return [(left_id, right_id, score) for (left_id, right_id), score in pairs.items()]

    def semantic_similarity(
        self,
        left_text: str,
        right_text: str,
        left_vector: list[float] | None,
        right_vector: list[float] | None,
    ) -> float:
        if self.mode != "llm":
            return cosine_similarity(left_vector, right_vector)
        left_key = self._key(normalize_text(left_text))
        right_key = self._key(normalize_text(right_text))
        if left_key == right_key:
            return 1.0
        return self._task_pair_scores.get(frozenset((left_key, right_key)), 0.0)

    def _record_llm_usage(
        self,
        *,
        response: Any,
        stats: EmbeddingBatchStats,
        status: str,
        error_message: str | None = None,
    ) -> None:
        try:
            from backend.services.usage_recorder import record_llm_usage

            record_llm_usage(
                response=response,
                latency_ms=stats.latency_ms,
                status=status,
                error_message=error_message,
                model=self.model,
            )
        except Exception:
            logger.warning("Semantic LLM usage recording failed", exc_info=True)

    def _record_usage(self, stats: EmbeddingBatchStats) -> None:
        if (
            stats.external_inputs == 0
            and stats.failed_batches == 0
            and stats.cache_hits == 0
        ):
            return
        try:
            from backend.services.usage_recorder import record_embedding_usage

            record_embedding_usage(
                provider=self.provider,
                model=self.model,
                status="success" if stats.failed_batches == 0 else "error",
                latency_ms=stats.latency_ms,
                input_count=stats.external_inputs,
                input_chars=stats.input_chars,
                cache_hits=stats.cache_hits,
                error_message=stats.degraded_reason,
            )
        except Exception:
            logger.warning("Embedding usage recording failed", exc_info=True)

    async def get_embedding(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        if not vectors or vectors[0] is None:
            raise RuntimeError(self.last_stats.degraded_reason or "embedding unavailable")
        return vectors[0]

    async def embed(self, text: str) -> list[float]:
        return await self.get_embedding(text)

    async def compute_similarity(self, text1: str, text2: str) -> float:
        left, right = await self.embed_batch([text1, text2])
        return self.semantic_similarity(text1, text2, left, right)

    def merge_candidates(
        self,
        existing: dict,
        new: dict,
        similarity_threshold: float = 0.85,
    ) -> tuple[dict | None, bool]:
        """Legacy synchronous merge helper retained for compatibility."""

        existing_text = self._build_comparison_text(existing)
        new_text = self._build_comparison_text(new)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            similarity = asyncio.run(self.compute_similarity(existing_text, new_text))
        else:
            raise RuntimeError("merge_candidates cannot block inside an active event loop")
        if similarity < similarity_threshold:
            return None, False
        existing_rank = SEVERITY_ORDER.get(existing.get("severity", "minor"), 0)
        new_rank = SEVERITY_ORDER.get(new.get("severity", "minor"), 0)
        return (new if new_rank >= existing_rank else existing), True

    @staticmethod
    def _build_comparison_text(record: dict) -> str:
        return " ".join(
            str(record[field])
            for field in ("requirement_content", "bid_content", "explanation", "suggestion")
            if record.get(field)
        )


__all__ = [
    "EmbeddingBatchStats",
    "EmbeddingService",
    "SEVERITY_ORDER",
    "cosine_similarity",
]
