"""Global candidate index for 3–10 document duplicate tasks.

The index builds one cross-document graph.  Exact hashes form evidence
clusters first; lexical/semantic/table/image channels add bounded cross-party
edges.  The rule master is invoked once per rule over this shared index, so
the number of LLM flows does not grow with the number of document pairs.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from backend.services.duplicate_candidates import (
    DocumentBlock,
    DocumentDescriptor,
    DuplicateCandidate,
    DuplicateCandidateService,
    _char_ngrams,
    calculate_evidence_strength,
    normalize_text,
    parse_evidence_blocks,
)
from backend.services.duplicate_image_evidence import perceptual_similarity
from backend.services.duplicate_tables import compare_table_blocks
from backend.services.embedding_service import EmbeddingService, cosine_similarity

logger = logging.getLogger(__name__)


class MultiDocumentCandidateService:
    def __init__(
        self,
        documents: Iterable[DocumentDescriptor],
        *,
        max_candidates: int = 1200,
        embedding_service: EmbeddingService | None = None,
        semantic_enabled: bool = False,
        semantic_min_score: float = 0.72,
        max_semantic_blocks: int = 400,
        semantic_min_chars: int = 24,
        candidate_min_score: float = 0.45,
        lexical_min_score: float = 0.16,
        structure_min_score: float = 0.50,
        near_exact_min_score: float = 0.72,
        image_min_score: float = 0.78,
        algorithm_version: str = "duplicate-batch/s2-4.1",
    ):
        self.documents = list(documents)
        self.max_candidates = max(1, int(max_candidates))
        self.embedding_service = embedding_service
        self.semantic_enabled = bool(semantic_enabled and embedding_service is not None)
        self.semantic_min_score = min(1.0, max(0.0, float(semantic_min_score)))
        self.max_semantic_blocks = max(2, int(max_semantic_blocks))
        self.semantic_min_chars = max(12, int(semantic_min_chars))
        self.candidate_min_score = min(1.0, max(0.0, float(candidate_min_score)))
        self.lexical_min_score = min(1.0, max(0.0, float(lexical_min_score)))
        self.structure_min_score = min(1.0, max(0.0, float(structure_min_score)))
        self.near_exact_min_score = min(1.0, max(0.0, float(near_exact_min_score)))
        self.image_min_score = min(1.0, max(0.0, float(image_min_score)))
        self.algorithm_version = str(algorithm_version)
        self.blocks_by_document: dict[str, list[DocumentBlock]] = {}
        self.blocks: list[DocumentBlock] = []
        # Compatibility for tools expecting pair service attributes.
        self.left_blocks: list[DocumentBlock] = self.blocks
        self.right_blocks: list[DocumentBlock] = self.blocks
        self.candidates: list[DuplicateCandidate] = []
        self.warnings: list[str] = []
        self.exact_clusters: list[dict] = []

    async def build(self) -> list[DuplicateCandidate]:
        self.blocks_by_document = {
            descriptor.id: parse_evidence_blocks(
                descriptor, f"party-{index + 1}"
            )
            for index, descriptor in enumerate(self.documents)
        }
        self.blocks = [
            block
            for document_id in self.blocks_by_document
            for block in self.blocks_by_document[document_id]
        ]
        self.left_blocks = self.blocks
        self.right_blocks = self.blocks
        self.exact_clusters = self._build_exact_clusters()
        pool = [*self._exact_pool(), *self._lexical_pool(), *self._image_pool()]
        if self.semantic_enabled:
            try:
                pool.extend(await self._semantic_pool())
            except Exception as exc:
                self.warnings.append(f"embedding_degraded:{type(exc).__name__}")
                logger.warning("Batch semantic channel degraded: %s", exc)
        pool = DuplicateCandidateService._merge_channel_pool(pool)
        self.candidates = self._finalize(pool)
        return self.candidates

    def _build_exact_clusters(self) -> list[dict]:
        by_hash: dict[tuple[str, str], list[DocumentBlock]] = defaultdict(list)
        for block in self.blocks:
            if block.normalized and len(block.normalized) >= 8:
                by_hash[("text", block.normalized)].append(block)
            if block.image_sha256:
                by_hash[("image", block.image_sha256)].append(block)
        clusters: list[dict] = []
        for (kind, key), blocks in by_hash.items():
            document_ids = sorted({block.document_id for block in blocks})
            if len(document_ids) < 2:
                continue
            clusters.append(
                {
                    "cluster_key": sha256(f"{kind}\0{key}".encode()).hexdigest()[:32],
                    "content_type": "image" if kind == "image" else blocks[0].content_type,
                    "hash": key,
                    "document_ids": document_ids,
                    "occurrences": [
                        {
                            "document_id": block.document_id,
                            "block_id": block.id,
                            "excerpt": block.text,
                            "location": block.location(),
                        }
                        for block in blocks
                    ],
                }
            )
        return clusters

    def _exact_pool(self) -> list[dict]:
        pool: list[dict] = []
        for cluster in self.exact_clusters:
            # One representative edge per cluster; all locations remain in
            # cluster occurrences.  This prevents a 10-document exact match
            # from becoming 45 duplicate LLM candidates/findings.
            by_document: dict[str, dict] = {}
            for occurrence in cluster["occurrences"]:
                by_document.setdefault(occurrence["document_id"], occurrence)
            representatives = list(by_document.values())
            if len(representatives) < 2:
                continue
            left_item, right_item = representatives[:2]
            left = next(block for block in self.blocks if block.id == left_item["block_id"])
            right = next(block for block in self.blocks if block.id == right_item["block_id"])
            pool.append(
                {
                    "left": left,
                    "right": right,
                    "lexical": 1.0 if cluster["content_type"] != "image" else 0.0,
                    "structure": 0.0,
                    "semantic": 0.0,
                    "image": 1.0 if cluster["content_type"] == "image" else 0.0,
                    "image_exact": cluster["content_type"] == "image",
                    "table": None,
                }
            )
        return pool

    def _cross_document_pairs(self) -> Iterable[tuple[DocumentBlock, DocumentBlock]]:
        for index, left in enumerate(self.blocks):
            for right in self.blocks[index + 1 :]:
                if left.document_id != right.document_id:
                    yield left, right

    def _lexical_pool(self) -> list[dict]:
        right_grams: dict[str, set[str]] = {
            block.id: _char_ngrams(block.normalized) for block in self.blocks
        }
        inverted: dict[str, list[str]] = defaultdict(list)
        for block in self.blocks:
            for gram in right_grams[block.id]:
                inverted[gram].append(block.id)
        by_id = {block.id: block for block in self.blocks}
        pool: list[dict] = []
        for left in self.blocks:
            if len(left.normalized) < 8:
                continue
            votes: Counter[str] = Counter()
            for gram in _char_ngrams(left.normalized):
                for block_id in inverted.get(gram, []):
                    if by_id[block_id].document_id != left.document_id:
                        votes[block_id] += 1
            for block_id, _ in votes.most_common(18):
                right = by_id[block_id]
                if left.normalized and left.normalized == right.normalized:
                    continue
                left_grams = _char_ngrams(left.normalized)
                right_set = right_grams[right.id]
                union = len(left_grams | right_set) or 1
                lexical = 0.55 * (len(left_grams & right_set) / union)
                from difflib import SequenceMatcher

                lexical += 0.45 * SequenceMatcher(
                    None, left.normalized, right.normalized
                ).ratio()
                number_union = set(left.numbers) | set(right.numbers)
                structure = (
                    len(set(left.numbers) & set(right.numbers)) / len(number_union)
                    if number_union
                    else 0.0
                )
                table = None
                if left.evidence_block is not None and right.evidence_block is not None:
                    matches = compare_table_blocks(
                        [left.evidence_block], [right.evidence_block], limit=1
                    )
                    table = matches[0] if matches else None
                    if table:
                        structure = max(structure, table.score)
                if lexical < self.lexical_min_score and structure < self.structure_min_score:
                    continue
                pool.append(
                    {
                        "left": left,
                        "right": right,
                        "lexical": lexical,
                        "structure": structure,
                        "semantic": 0.0,
                        "image": 0.0,
                        "table": table,
                    }
                )
        return pool

    def _image_pool(self) -> list[dict]:
        images = [block for block in self.blocks if block.content_type == "image"]
        pool: list[dict] = []
        for index, left in enumerate(images):
            for right in images[index + 1 :]:
                if left.document_id == right.document_id:
                    continue
                exact = bool(
                    left.image_sha256
                    and right.image_sha256
                    and left.image_sha256 == right.image_sha256
                )
                if exact:
                    continue  # represented once by the exact cluster channel
                score = (
                    1.0
                    if exact
                    else perceptual_similarity(left.perceptual_hash, right.perceptual_hash)
                )
                if score >= self.image_min_score:
                    pool.append(
                        {
                            "left": left,
                            "right": right,
                            "lexical": 0.0,
                            "structure": 0.0,
                            "semantic": 0.0,
                            "image": score,
                            "image_exact": exact,
                            "table": None,
                        }
                    )
        return pool

    async def _semantic_pool(self) -> list[dict]:
        eligible = [
            block
            for block in self.blocks
            if block.content_type in {"paragraph", "image_ocr"}
            and self.semantic_min_chars <= len(block.normalized) <= 4000
        ][: self.max_semantic_blocks]
        if len(eligible) < 2 or self.embedding_service is None:
            return []
        vectors = await self.embedding_service.embed_batch([block.text for block in eligible])
        pool: list[dict] = []
        for index, left in enumerate(eligible):
            if vectors[index] is None:
                continue
            ranked: list[tuple[float, int]] = []
            for right_index in range(index + 1, len(eligible)):
                right = eligible[right_index]
                if left.document_id == right.document_id or vectors[right_index] is None:
                    continue
                score = cosine_similarity(vectors[index], vectors[right_index])
                if score >= self.semantic_min_score:
                    ranked.append((score, right_index))
            for score, right_index in sorted(ranked, reverse=True)[:8]:
                pool.append(
                    {
                        "left": left,
                        "right": eligible[right_index],
                        "lexical": 0.0,
                        "structure": 0.0,
                        "semantic": score,
                        "image": 0.0,
                        "table": None,
                    }
                )
        return pool

    def _finalize(self, pool: list[dict]) -> list[DuplicateCandidate]:
        results: list[DuplicateCandidate] = []
        seen: set[tuple[str, str]] = set()
        occurrences = Counter(block.normalized for block in self.blocks)
        by_id = {block.id: block for block in self.blocks}
        for item in sorted(
            pool,
            key=lambda value: max(
                float(value.get("lexical") or 0),
                float(value.get("structure") or 0),
                float(value.get("semantic") or 0),
                float(value.get("image") or 0),
            ),
            reverse=True,
        ):
            left: DocumentBlock = item["left"]
            right: DocumentBlock = item["right"]
            key = tuple(sorted((left.id, right.id)))
            if key in seen:
                continue
            seen.add(key)
            lexical = float(item.get("lexical") or 0)
            structure = float(item.get("structure") or 0)
            semantic = float(item.get("semantic") or 0)
            image = float(item.get("image") or 0)
            table = item.get("table")
            score = max(lexical, 0.85 * structure, 0.96 * semantic, image)
            if item.get("image_exact") or left.normalized == right.normalized:
                score, match_type = 1.0, "exact"
            elif image >= self.image_min_score:
                match_type = "near_exact"
            elif semantic >= self.semantic_min_score and semantic > lexical:
                match_type = "semantic"
            elif table is not None or structure >= 0.65:
                match_type = "structural"
            elif lexical >= self.near_exact_min_score:
                match_type = "near_exact"
            else:
                match_type = "near_exact"
            if score < self.candidate_min_score:
                continue
            normalized_length = min(len(left.normalized), len(right.normalized))
            evidence_strength = calculate_evidence_strength(
                normalized_length=normalized_length,
                left_occurrences=occurrences[left.normalized],
                right_occurrences=occurrences[right.normalized],
                shared_number_count=len(set(left.numbers) & set(right.numbers)),
                similarity_score=score,
            )
            digest = sha256(f"{key[0]}:{key[1]}".encode()).hexdigest()[:16]
            results.append(
                DuplicateCandidate(
                    id=digest,
                    left=left,
                    right=right,
                    similarity_score=min(1.0, score),
                    lexical_score=lexical,
                    structure_score=structure,
                    match_type=match_type,
                    evidence_strength=evidence_strength,
                    rank_score=0.52 * score + 0.48 * evidence_strength,
                    normalized_length=normalized_length,
                    left_occurrences=occurrences[left.normalized],
                    right_occurrences=occurrences[right.normalized],
                    semantic_score=semantic,
                    image_score=image,
                    header_similarity=table.header_similarity if table else 0.0,
                    row_alignment_score=table.row_alignment_score if table else 0.0,
                    numeric_signature_score=table.numeric_signature_score if table else 0.0,
                    rare_cell_overlap=table.rare_cell_overlap if table else 0.0,
                    table_structure_score=table.table_structure_score if table else 0.0,
                    shared_rare_cells=table.shared_rare_cells if table else [],
                )
            )
        results.sort(
            key=lambda value: (
                value.rank_score,
                value.evidence_strength,
                value.similarity_score,
            ),
            reverse=True,
        )
        return results[: self.max_candidates]

    def search(self, query: str = "", *, limit: int = 30) -> list[DuplicateCandidate]:
        if not query.strip():
            return self.candidates[:limit]
        query_grams = _char_ngrams(normalize_text(query))
        ranked: list[tuple[float, DuplicateCandidate]] = []
        for candidate in self.candidates:
            haystack = normalize_text(
                f"{candidate.left.section} {candidate.left.text} "
                f"{candidate.right.section} {candidate.right.text}"
            )
            grams = _char_ngrams(haystack)
            overlap = len(query_grams & grams) / (min(len(query_grams), len(grams)) or 1)
            ranked.append((0.65 * candidate.rank_score + 0.35 * overlap, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in ranked[:limit]]

    def get(self, candidate_id: str) -> DuplicateCandidate | None:
        return next((candidate for candidate in self.candidates if candidate.id == candidate_id), None)

    def get_context(self, candidate_id: str, *, radius: int = 1) -> dict | None:
        candidate = self.get(candidate_id)
        if candidate is None:
            return None

        def context(block: DocumentBlock) -> dict:
            blocks = self.blocks_by_document.get(block.document_id, [])
            index = next((i for i, item in enumerate(blocks) if item.id == block.id), -1)
            radius_value = max(0, min(int(radius), 3))
            return {
                "before": [
                    item.to_context_dict() for item in blocks[max(0, index - radius_value) : index]
                ],
                "current": block.to_context_dict(),
                "after": [
                    item.to_context_dict()
                    for item in blocks[index + 1 : index + 1 + radius_value]
                ],
            }

        payload = candidate.to_agent_dict()
        payload["left_context"] = context(candidate.left)
        payload["right_context"] = context(candidate.right)
        return payload

    def save_cache(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": "duplicate-candidates-batch/v1",
                    "algorithm_version": self.algorithm_version,
                    "document_ids": [document.id for document in self.documents],
                    "candidates": [candidate.to_agent_dict() for candidate in self.candidates],
                    "clusters": self.exact_clusters,
                    "warnings": self.warnings,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def pair_statistics(self) -> dict[tuple[str, str], dict]:
        stats: dict[tuple[str, str], dict] = {}
        for cluster in self.exact_clusters:
            document_ids = sorted(set(cluster["document_ids"]))
            for index, left_document_id in enumerate(document_ids):
                for right_document_id in document_ids[index + 1 :]:
                    pair = (left_document_id, right_document_id)
                    item = stats.setdefault(
                        pair,
                        {
                            "candidate_count": 0,
                            "max_evidence_strength": 0.0,
                            "channel_hits": {"lexical": 0, "structure": 0, "semantic": 0, "image": 0},
                        },
                    )
                    item["candidate_count"] += 1
                    channel = "image" if cluster["content_type"] == "image" else "lexical"
                    item["channel_hits"][channel] += 1
                    item["max_evidence_strength"] = max(
                        item["max_evidence_strength"], 1.0
                    )
        for candidate in self.candidates:
            pair = tuple(sorted((candidate.left.document_id, candidate.right.document_id)))
            represents_exact_cluster = bool(
                (candidate.left.normalized and candidate.left.normalized == candidate.right.normalized)
                or (
                    candidate.left.image_sha256
                    and candidate.left.image_sha256 == candidate.right.image_sha256
                )
            )
            if represents_exact_cluster:
                continue
            item = stats.setdefault(
                pair,
                {
                    "candidate_count": 0,
                    "max_evidence_strength": 0.0,
                    "channel_hits": {"lexical": 0, "structure": 0, "semantic": 0, "image": 0},
                },
            )
            item["candidate_count"] += 1
            item["max_evidence_strength"] = max(
                item["max_evidence_strength"], candidate.evidence_strength
            )
            for channel, score in (
                ("lexical", candidate.lexical_score),
                ("structure", candidate.structure_score),
                ("semantic", candidate.semantic_score),
                ("image", candidate.image_score),
            ):
                if score > 0:
                    item["channel_hits"][channel] += 1
        return stats


__all__ = ["MultiDocumentCandidateService"]
