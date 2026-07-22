"""Candidate recall for document pairs without reliable chapter structure."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .duplicate_rule_loader import DuplicateRuleConfig


@dataclass
class TextChunk:
    chunk_id: str
    text: str
    start_char: int
    end_char: int


def _normalise(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", "", text)
    return re.sub(r"[，。！？；：、,.!?;:'\"“”‘’（）()\[\]【】<>《》]", "", text).lower()


def chunk_document(path: str | Path, config: DuplicateRuleConfig) -> list[TextChunk]:
    text = Path(path).read_text(encoding="utf-8")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[TextChunk] = []
    buffer = ""
    cursor = 0
    start = 0
    for paragraph in paragraphs:
        if not buffer:
            start = text.find(paragraph, cursor)
            if start < 0:
                start = cursor
        if buffer and len(buffer) + len(paragraph) + 2 > config.chunk_size_chars:
            chunks.append(TextChunk(f"c{len(chunks)+1}", buffer, start, start + len(buffer)))
            overlap = buffer[-config.chunk_overlap_chars :] if config.chunk_overlap_chars else ""
            buffer = overlap + "\n\n" + paragraph if overlap else paragraph
            start = max(0, start + len(chunks[-1].text) - len(overlap))
        else:
            buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        cursor = max(cursor, start + len(buffer))
    if buffer:
        chunks.append(TextChunk(f"c{len(chunks)+1}", buffer, start, start + len(buffer)))
    return chunks


def _excluded(text: str, config: DuplicateRuleConfig) -> bool:
    compact = _normalise(text)
    if len(compact) < config.min_evidence_chars:
        return True
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in config.exclude_text_patterns)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    ma = math.sqrt(sum(x * x for x in a))
    mb = math.sqrt(sum(y * y for y in b))
    return dot / (ma * mb) if ma and mb else 0.0


def _trigrams(text: str) -> set[str]:
    value = _normalise(text)
    if len(value) < 3:
        return {value} if value else set()
    return {value[index:index + 3] for index in range(len(value) - 2)}


async def recall_candidates(
    path_a: str,
    path_b: str,
    config: DuplicateRuleConfig,
    *,
    use_embeddings: bool = True,
) -> tuple[list[dict], dict]:
    chunks_a = [c for c in chunk_document(path_a, config) if not _excluded(c.text, config)]
    chunks_b = [c for c in chunk_document(path_b, config) if not _excluded(c.text, config)]
    candidates: dict[tuple[str, str], dict] = {}

    # Lexical recall is always available and deterministic. Use an inverted
    # trigram index to avoid O(N*M) full SequenceMatcher comparisons on very
    # large bid documents; only the strongest overlap candidates get the
    # comparatively expensive fuzzy comparison.
    grams_b = [_trigrams(chunk.text) for chunk in chunks_b]
    inverted: dict[str, list[int]] = defaultdict(list)
    for index_b, grams in enumerate(grams_b):
        for gram in grams:
            inverted[gram].append(index_b)
    for a in chunks_a:
        overlap_counts: Counter[int] = Counter()
        for gram in _trigrams(a.text):
            overlap_counts.update(inverted.get(gram, []))
        preselected = [index for index, _ in overlap_counts.most_common(config.top_k_per_chunk * 6)]
        scored = []
        na = _normalise(a.text)
        for index_b in preselected:
            b = chunks_b[index_b]
            score = SequenceMatcher(None, na, _normalise(b.text), autojunk=False).ratio()
            if score >= config.lexical_threshold:
                scored.append((score, b))
        for score, b in sorted(scored, key=lambda item: item[0], reverse=True)[: config.top_k_per_chunk]:
            candidates[(a.chunk_id, b.chunk_id)] = {
                "candidate_id": f"{a.chunk_id}_{b.chunk_id}",
                "document_a": {"chunk_id": a.chunk_id, "excerpt": a.text, "start_char": a.start_char},
                "document_b": {"chunk_id": b.chunk_id, "excerpt": b.text, "start_char": b.start_char},
                "recall_type": "lexical",
                "internal_score": score,
            }

    embedding_error = None
    if use_embeddings and chunks_a and chunks_b:
        try:
            from backend.services.embedding_service import EmbeddingService
            service = EmbeddingService()
            embeddings = await service.get_embeddings([c.text for c in chunks_a + chunks_b])
            import numpy as np
            vectors_a = np.asarray(embeddings[: len(chunks_a)], dtype=np.float32)
            vectors_b = np.asarray(embeddings[len(chunks_a) :], dtype=np.float32)
            vectors_a /= np.maximum(np.linalg.norm(vectors_a, axis=1, keepdims=True), 1e-12)
            vectors_b /= np.maximum(np.linalg.norm(vectors_b, axis=1, keepdims=True), 1e-12)
            # Bounded blocks keep the temporary similarity matrix small.
            for start in range(0, len(chunks_a), 64):
                block = vectors_a[start:start + 64] @ vectors_b.T
                top_k = min(config.top_k_per_chunk, len(chunks_b))
                top_indices = np.argpartition(block, -top_k, axis=1)[:, -top_k:]
                for row, indices in enumerate(top_indices):
                    index_a = start + row
                    a = chunks_a[index_a]
                    for index_b in indices:
                        score = float(block[row, index_b])
                        if score < config.semantic_threshold:
                            continue
                        b = chunks_b[int(index_b)]
                        key = (a.chunk_id, b.chunk_id)
                        existing = candidates.get(key)
                        candidates[key] = {
                            "candidate_id": f"{a.chunk_id}_{b.chunk_id}",
                            "document_a": {"chunk_id": a.chunk_id, "excerpt": a.text, "start_char": a.start_char},
                            "document_b": {"chunk_id": b.chunk_id, "excerpt": b.text, "start_char": b.start_char},
                            "recall_type": "lexical+semantic" if existing else "semantic",
                            "internal_score": max(score, existing["internal_score"] if existing else 0),
                        }
        except Exception as exc:
            embedding_error = str(exc)

    ranked = sorted(candidates.values(), key=lambda item: item["internal_score"], reverse=True)
    ranked = ranked[: config.max_candidates_per_pair]
    return ranked, {
        "chunks_a": len(chunks_a),
        "chunks_b": len(chunks_b),
        "candidate_count": len(ranked),
        "embedding_error": embedding_error,
    }
