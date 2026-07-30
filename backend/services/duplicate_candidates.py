"""Deterministic A/B candidate retrieval for technical bid duplicate checks."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from backend.schemas.document_artifacts import DuplicateEvidenceBlock
from backend.services.document_artifacts import load_evidence_blocks
from backend.services.duplicate_tables import compare_table_blocks
from backend.services.duplicate_image_evidence import perceptual_similarity
from backend.services.embedding_service import EmbeddingService

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
# ``\w`` treats Chinese characters as word characters.  That made values such
# as ``质保3年`` and ``型号AB-123`` disappear.  Boundary checks are deliberately
# limited to ASCII identifier characters so Chinese prose can surround a
# numeric/model token without blocking extraction.
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?=[A-Z0-9_\-/]*\d)[A-Z][A-Z0-9_]*(?:[-/][A-Z0-9]+)*"
    r"|\d+(?:\.\d+)?%?"
    r")(?![A-Za-z0-9_])",
    re.I,
)
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


@dataclass(slots=True)
class DocumentDescriptor:
    id: str
    filename: str
    path: str
    evidence_blocks_path: str | None = None
    role: str | None = None
    source_basis: str = "bidder_authored"


@dataclass(slots=True)
class DocumentBlock:
    id: str
    side: str
    document_id: str
    filename: str
    section: str
    start_line: int
    end_line: int
    text: str
    normalized: str
    numbers: list[str] = field(default_factory=list)
    content_type: str = "paragraph"
    source_basis: str = "bidder_authored"
    page_number: int | None = None
    bbox: dict[str, float] | None = None
    table_id: str | None = None
    row_index: int | None = None
    column_index: int | None = None
    header_map: dict[str, str] | None = None
    image_path: str | None = None
    image_sha256: str | None = None
    perceptual_hash: str | None = None
    ocr_confidence: float | None = None
    evidence_block: DuplicateEvidenceBlock | None = None

    def location(self) -> dict:
        return {
            "section": self.section,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "content_type": self.content_type,
            "page_number": self.page_number,
            "bbox": self.bbox,
            "table_id": self.table_id,
            "row_index": self.row_index,
            "column_index": self.column_index,
            "header_map": self.header_map,
            "image_path": self.image_path,
        }

    def to_context_dict(self, *, max_chars: int = 500) -> dict:
        return {
            "id": self.id,
            "section": self.section,
            "location": self.location(),
            "text": self.text[:max_chars],
            "normalized_length": len(self.normalized),
            "numbers": self.numbers[:20],
            "source_basis": self.source_basis,
        }


@dataclass(slots=True)
class DuplicateCandidate:
    id: str
    left: DocumentBlock
    right: DocumentBlock
    similarity_score: float
    lexical_score: float
    structure_score: float
    match_type: str
    evidence_strength: float = 0.0
    rank_score: float = 0.0
    normalized_length: int = 0
    left_occurrences: int = 1
    right_occurrences: int = 1
    semantic_score: float = 0.0
    image_score: float = 0.0
    header_similarity: float = 0.0
    row_alignment_score: float = 0.0
    numeric_signature_score: float = 0.0
    rare_cell_overlap: float = 0.0
    table_structure_score: float = 0.0
    shared_rare_cells: list[str] = field(default_factory=list)

    def to_agent_dict(self) -> dict:
        return {
            "candidate_id": self.id,
            "left_document_id": self.left.document_id,
            "right_document_id": self.right.document_id,
            "left_block_id": self.left.id,
            "right_block_id": self.right.id,
            "similarity_score": round(self.similarity_score, 4),
            "lexical_score": round(self.lexical_score, 4),
            "structure_score": round(self.structure_score, 4),
            "evidence_strength": round(self.evidence_strength, 4),
            "rank_score": round(self.rank_score, 4),
            "hybrid_rank": round(self.rank_score, 4),
            "normalized_length": self.normalized_length,
            "left_occurrences": self.left_occurrences,
            "right_occurrences": self.right_occurrences,
            "match_type": self.match_type,
            "semantic_score": round(self.semantic_score, 4),
            "image_score": round(self.image_score, 4),
            "source_basis": (
                self.left.source_basis
                if self.left.source_basis == self.right.source_basis
                else "unknown"
            ),
            "left_excerpt": self.left.text,
            "left_location": self.left.location(),
            "right_excerpt": self.right.text,
            "right_location": self.right.location(),
            "table_comparison": (
                {
                    "header_similarity": round(self.header_similarity, 4),
                    "row_alignment_score": round(self.row_alignment_score, 4),
                    "numeric_signature_score": round(self.numeric_signature_score, 4),
                    "rare_cell_overlap": round(self.rare_cell_overlap, 4),
                    "table_structure_score": round(self.table_structure_score, 4),
                    "shared_rare_cells": self.shared_rare_cells,
                    "left_cells": (
                        [cell.strip() for cell in self.left.text.split("|")]
                        if self.left.content_type == "table_row"
                        else []
                    ),
                    "right_cells": (
                        [cell.strip() for cell in self.right.text.split("|")]
                        if self.right.content_type == "table_row"
                        else []
                    ),
                }
                if self.left.content_type in {"table", "table_row"}
                or self.right.content_type in {"table", "table_row"}
                else None
            ),
            "image_comparison": (
                {
                    "left_image_sha256": self.left.image_sha256,
                    "right_image_sha256": self.right.image_sha256,
                    "left_perceptual_hash": self.left.perceptual_hash,
                    "right_perceptual_hash": self.right.perceptual_hash,
                    "image_score": round(self.image_score, 4),
                    "left_ocr_confidence": self.left.ocr_confidence,
                    "right_ocr_confidence": self.right.ocr_confidence,
                }
                if self.image_score > 0
                else None
            ),
        }


def normalize_text(text: str) -> str:
    """Normalize layout noise while preserving Chinese, letters and numbers."""
    return _PUNCT_RE.sub("", _SPACE_RE.sub("", text)).lower()


def calculate_evidence_strength(
    *,
    normalized_length: int,
    left_occurrences: int = 1,
    right_occurrences: int = 1,
    shared_number_count: int = 0,
    similarity_score: float = 1.0,
) -> float:
    """Estimate how diagnostically unusual a similar pair is.

    This is intentionally separate from ``similarity_score``.  Long, unique
    passages are stronger evidence than a short boilerplate phrase, while a
    phrase repeated throughout either document is down-weighted.  Shared
    numbers/model identifiers provide a small, bounded boost.
    """

    length_factor = min(1.0, max(0, normalized_length) / 80.0)
    max_occurrences = max(1, left_occurrences, right_occurrences)
    rarity_factor = 1.0 / math.sqrt(max_occurrences)
    identifier_factor = min(1.0, max(0, shared_number_count) / 3.0)
    base = (
        0.55 * length_factor
        + 0.30 * rarity_factor
        + 0.15 * identifier_factor
    )
    similarity_factor = 0.65 + 0.35 * min(1.0, max(0.0, similarity_score))
    return round(min(1.0, max(0.0, base * similarity_factor)), 4)


def _char_ngrams(text: str, n: int = 2) -> set[str]:
    if len(text) <= n:
        return {text} if text else set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _split_long_block(text: str, max_chars: int = 1600) -> Iterable[str]:
    text = text.strip()
    if len(text) <= max_chars:
        if text:
            yield text
        return
    cursor = 0
    while cursor < len(text):
        end = min(cursor + max_chars, len(text))
        if end < len(text):
            boundary = max(text.rfind(mark, cursor + max_chars // 2, end) for mark in "。；\n")
            if boundary > cursor:
                end = boundary + 1
        part = text[cursor:end].strip()
        if part:
            yield part
        cursor = end


def parse_markdown_blocks(descriptor: DocumentDescriptor, side: str) -> list[DocumentBlock]:
    path = Path(descriptor.path)
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    section_stack: list[str] = []
    blocks: list[DocumentBlock] = []
    buffer: list[str] = []
    start_line = 1

    def flush(end_line: int) -> None:
        nonlocal buffer, start_line
        raw = "\n".join(buffer).strip()
        buffer = []
        # Legacy Markdown may still contain inline image/data-URI references.
        # They are handled only by the image channel and must never become
        # exact textual candidates.
        raw = _MARKDOWN_IMAGE_RE.sub("", raw).strip()
        if not raw:
            return
        offset = 0
        for part in _split_long_block(raw):
            normalized = normalize_text(part)
            if len(normalized) < 12:
                offset += part.count("\n") + 1
                continue
            block_id = f"{side}-{len(blocks) + 1}"
            part_start = start_line + offset
            part_end = min(end_line, part_start + part.count("\n"))
            blocks.append(
                DocumentBlock(
                    id=block_id,
                    side=side,
                    document_id=descriptor.id,
                    filename=descriptor.filename,
                    section=" / ".join(section_stack) or "正文",
                    start_line=part_start,
                    end_line=part_end,
                    text=part[:2000],
                    normalized=normalized[:4000],
                    numbers=[token.upper() for token in _NUMBER_RE.findall(part)],
                    content_type=(
                        "table"
                        if any(
                            line.count("|") >= 2 or "<table" in line.lower()
                            for line in part.splitlines()
                        )
                            else "paragraph"
                    ),
                    source_basis=descriptor.source_basis,
                )
            )
            offset += part.count("\n") + 1

    for line_no, line in enumerate(lines, 1):
        heading = _HEADING_RE.match(line)
        if heading:
            flush(line_no - 1)
            stripped = line.lstrip()
            level = len(stripped) - len(stripped.lstrip("#"))
            section_stack[:] = section_stack[: max(0, level - 1)]
            section_stack.append(heading.group(1).strip())
            start_line = line_no + 1
            continue
        if not line.strip():
            flush(line_no - 1)
            start_line = line_no + 1
            continue
        if not buffer:
            start_line = line_no
        buffer.append(line)
    flush(len(lines))
    return blocks


def parse_evidence_blocks(
    descriptor: DocumentDescriptor,
    side: str,
) -> list[DocumentBlock]:
    """Load S2 evidence IR, falling back to Markdown for legacy documents."""

    evidence_path = Path(descriptor.evidence_blocks_path) if descriptor.evidence_blocks_path else None
    if evidence_path is None or not evidence_path.is_file():
        return parse_markdown_blocks(descriptor, side)

    blocks: list[DocumentBlock] = []
    for evidence in load_evidence_blocks(evidence_path):
        if evidence.content_type not in {
            "paragraph",
            "heading",
            "table",
            "table_row",
            "image",
            "image_ocr",
            "caption",
        }:
            continue
        normalized = normalize_text(evidence.raw_text)
        if evidence.content_type not in {"image"} and len(normalized) < 4:
            continue
        blocks.append(
            DocumentBlock(
                id=evidence.block_id,
                side=side,
                document_id=descriptor.id,
                filename=descriptor.filename,
                section=" / ".join(evidence.section_path) or "正文",
                start_line=evidence.start_line or 0,
                end_line=evidence.end_line or evidence.start_line or 0,
                text=evidence.raw_text[:4000],
                normalized=normalized[:8000],
                numbers=[value.upper() for value in evidence.numbers],
                content_type=evidence.content_type,
                source_basis=descriptor.source_basis,
                page_number=evidence.page_number,
                bbox=evidence.bbox,
                table_id=evidence.table_id,
                row_index=evidence.row_index,
                column_index=evidence.column_index,
                header_map=evidence.header_map,
                image_path=evidence.image_path,
                image_sha256=evidence.image_sha256,
                perceptual_hash=evidence.perceptual_hash,
                ocr_confidence=evidence.ocr_confidence,
                evidence_block=evidence,
            )
        )
    return blocks or parse_markdown_blocks(descriptor, side)


class DuplicateCandidateService:
    """Build and query a task-scoped A/B candidate index."""

    def __init__(
        self,
        left: DocumentDescriptor,
        right: DocumentDescriptor,
        *,
        max_candidates: int = 400,
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
        algorithm_version: str = "duplicate-candidates/s2-4.2",
    ):
        self.left_doc = left
        self.right_doc = right
        self.max_candidates = max_candidates
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
        self.left_blocks: list[DocumentBlock] = []
        self.right_blocks: list[DocumentBlock] = []
        self.candidates: list[DuplicateCandidate] = []
        self.warnings: list[str] = []

    async def build(self) -> list[DuplicateCandidate]:
        """Build the fully local, deterministic task-scoped candidate index."""
        self.left_blocks = parse_evidence_blocks(self.left_doc, "left")
        self.right_blocks = parse_evidence_blocks(self.right_doc, "right")
        semantic_pool: list[dict] = []
        if self.semantic_enabled:
            try:
                semantic_pool = await self._semantic_pool()
            except Exception as exc:
                self.warnings.append(f"embedding_degraded:{type(exc).__name__}")
                logger = __import__("logging").getLogger(__name__)
                logger.warning("Semantic candidate channel degraded: %s", exc)
        pool = self._merge_channel_pool(
            [*self._lexical_pool(), *self._image_pool(), *semantic_pool]
        )
        pool.sort(
            key=lambda item: max(
                float(item.get("lexical") or 0.0),
                float(item.get("structure") or 0.0),
                float(item.get("image") or 0.0),
            ),
            reverse=True,
        )
        self.candidates = self._finalize(pool)
        if self.semantic_enabled:
            self.candidates = self._apply_channel_quotas(self.candidates)
        return self.candidates

    @staticmethod
    def _merge_channel_pool(pool: list[dict]) -> list[dict]:
        merged: dict[tuple[str, str], dict] = {}
        for item in pool:
            key = (item["left"].id, item["right"].id)
            current = merged.get(key)
            if current is None:
                merged[key] = dict(item)
                continue
            for channel in ("lexical", "structure", "semantic", "image"):
                current[channel] = max(
                    float(current.get(channel) or 0.0),
                    float(item.get(channel) or 0.0),
                )
            current["image_exact"] = bool(
                current.get("image_exact") or item.get("image_exact")
            )
            if item.get("table") is not None:
                current["table"] = item["table"]
        return list(merged.values())

    def _lexical_pool(self) -> list[dict]:
        # Image blocks are compared by SHA/perceptual hash only.  Their alt
        # names and legacy data-URI placeholders are not textual evidence.
        left_blocks = [block for block in self.left_blocks if block.content_type != "image"]
        right_blocks = [block for block in self.right_blocks if block.content_type != "image"]
        right_grams = [_char_ngrams(b.normalized) for b in right_blocks]
        inverted: dict[str, list[int]] = defaultdict(list)
        for idx, grams in enumerate(right_grams):
            for gram in grams:
                inverted[gram].append(idx)

        pool: list[dict] = []
        for left in left_blocks:
            left_grams = _char_ngrams(left.normalized)
            votes: Counter[int] = Counter()
            for gram in left_grams:
                for idx in inverted.get(gram, ()):
                    votes[idx] += 1
            for right_idx, _ in votes.most_common(24):
                right = right_blocks[right_idx]
                rg = right_grams[right_idx]
                union = len(left_grams | rg) or 1
                jaccard = len(left_grams & rg) / union
                sequence = SequenceMatcher(None, left.normalized, right.normalized).ratio()
                lexical = 0.55 * jaccard + 0.45 * sequence
                number_union = set(left.numbers) | set(right.numbers)
                structure = (
                    len(set(left.numbers) & set(right.numbers)) / len(number_union)
                    if number_union
                    else 0.0
                )
                table_metrics = None
                if left.evidence_block is not None and right.evidence_block is not None:
                    table_matches = compare_table_blocks(
                        [left.evidence_block], [right.evidence_block], limit=1
                    )
                    if table_matches:
                        table_metrics = table_matches[0]
                        structure = max(structure, table_metrics.score)
                if lexical < self.lexical_min_score and structure < self.structure_min_score:
                    continue
                pool.append(
                    {
                        "left": left,
                        "right": right,
                        "lexical": lexical,
                        "structure": structure,
                        "table": table_metrics,
                    }
                )
        pool.sort(key=lambda item: max(item["lexical"], item["structure"]), reverse=True)
        return pool[: self.max_candidates * 2]

    def _image_pool(self) -> list[dict]:
        """Exact SHA and local perceptual-hash image channel."""

        left_images = [block for block in self.left_blocks if block.content_type == "image"]
        right_images = [block for block in self.right_blocks if block.content_type == "image"]
        pool: list[dict] = []
        for left in left_images:
            for right in right_images:
                exact = bool(
                    left.image_sha256
                    and right.image_sha256
                    and left.image_sha256 == right.image_sha256
                )
                image_score = (
                    1.0
                    if exact
                    else perceptual_similarity(left.perceptual_hash, right.perceptual_hash)
                )
                if image_score < self.image_min_score:
                    continue
                pool.append(
                    {
                        "left": left,
                        "right": right,
                        "lexical": 0.0,
                        "structure": 0.0,
                        "image": image_score,
                        "image_exact": exact,
                        "table": None,
                    }
                )
        pool.sort(key=lambda item: item["image"], reverse=True)
        return pool[: self.max_candidates]

    async def _semantic_pool(self) -> list[dict]:
        """Task-local semantic top neighbours; never produces a verdict."""

        if self.embedding_service is None:
            return []

        def eligible(block: DocumentBlock) -> bool:
            return (
                block.content_type in {"paragraph", "image_ocr"}
                and self.semantic_min_chars <= len(block.normalized) <= 4000
            )

        left = [block for block in self.left_blocks if eligible(block)][
            : self.max_semantic_blocks // 2
        ]
        right = [block for block in self.right_blocks if eligible(block)][
            : self.max_semantic_blocks // 2
        ]
        if not left or not right:
            return []
        vectors = await self.embedding_service.embed_batch(
            [block.text for block in (*left, *right)]
        )
        left_vectors = vectors[: len(left)]
        right_vectors = vectors[len(left) :]
        if self.embedding_service.last_stats.degraded_reason:
            self.warnings.append(self.embedding_service.last_stats.degraded_reason)

        pool: list[dict] = []
        for left_block, left_vector in zip(left, left_vectors):
            if left_vector is None:
                continue
            ranked: list[tuple[float, int]] = []
            for index, right_vector in enumerate(right_vectors):
                score = self.embedding_service.semantic_similarity(
                    left_block.text,
                    right[index].text,
                    left_vector,
                    right_vector,
                )
                if score >= self.semantic_min_score:
                    ranked.append((score, index))
            ranked.sort(reverse=True)
            for score, index in ranked[:8]:
                pool.append(
                    {
                        "left": left_block,
                        "right": right[index],
                        "lexical": 0.0,
                        "structure": 0.0,
                        "semantic": score,
                        "image": 0.0,
                        "table": None,
                    }
                )
        pool.sort(key=lambda item: item["semantic"], reverse=True)
        return pool[: self.max_candidates]

    def _finalize(self, pool: list[dict]) -> list[DuplicateCandidate]:
        results: list[DuplicateCandidate] = []
        seen: set[tuple[str, str]] = set()
        left_occurrences = Counter(block.normalized for block in self.left_blocks)
        right_occurrences = Counter(block.normalized for block in self.right_blocks)
        for item in pool:
            left: DocumentBlock = item["left"]
            right: DocumentBlock = item["right"]
            key = (left.id, right.id)
            if key in seen:
                continue
            seen.add(key)
            lexical = float(item["lexical"])
            structure = float(item["structure"])
            table = item.get("table")
            image_score = float(item.get("image") or 0.0)
            semantic_score = float(item.get("semantic") or 0.0)
            score = max(lexical, 0.85 * structure, image_score, 0.96 * semantic_score)
            if item.get("image_exact"):
                score, match_type = 1.0, "exact"
            elif image_score >= self.image_min_score:
                match_type = "near_exact"
            elif semantic_score >= self.semantic_min_score and semantic_score > lexical:
                match_type = "semantic"
            elif left.normalized == right.normalized:
                score, match_type = 1.0, "exact"
            elif lexical >= self.near_exact_min_score:
                match_type = "near_exact"
            elif table is not None or structure >= 0.65:
                match_type = "structural"
            else:
                match_type = "near_exact"
            if score < self.candidate_min_score:
                continue
            normalized_length = min(len(left.normalized), len(right.normalized))
            shared_number_count = len(set(left.numbers) & set(right.numbers))
            evidence_strength = calculate_evidence_strength(
                normalized_length=normalized_length,
                left_occurrences=left_occurrences[left.normalized],
                right_occurrences=right_occurrences[right.normalized],
                shared_number_count=shared_number_count,
                similarity_score=score,
            )
            # Keep textual similarity intact, but use a quality-aware rank for
            # the bounded candidate budget.  This prevents dozens of tiny exact
            # boilerplate matches from crowding out distinctive passages.
            rank_score = 0.52 * min(1.0, score) + 0.48 * evidence_strength
            digest = sha256(f"{left.id}:{right.id}".encode()).hexdigest()[:16]
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
                    rank_score=rank_score,
                    normalized_length=normalized_length,
                    left_occurrences=left_occurrences[left.normalized],
                    right_occurrences=right_occurrences[right.normalized],
                    image_score=image_score,
                    semantic_score=semantic_score,
                    header_similarity=(table.header_similarity if table else 0.0),
                    row_alignment_score=(table.row_alignment_score if table else 0.0),
                    numeric_signature_score=(table.numeric_signature_score if table else 0.0),
                    rare_cell_overlap=(table.rare_cell_overlap if table else 0.0),
                    table_structure_score=(table.table_structure_score if table else 0.0),
                    shared_rare_cells=(table.shared_rare_cells if table else []),
                )
            )
        results.sort(
            key=lambda c: (c.rank_score, c.evidence_strength, c.similarity_score),
            reverse=True,
        )
        return results[: self.max_candidates]

    def _apply_channel_quotas(
        self, candidates: list[DuplicateCandidate]
    ) -> list[DuplicateCandidate]:
        """Reserve top-K capacity per channel before a deterministic fill."""

        if not candidates:
            return []
        limits = {
            "lexical": max(1, round(self.max_candidates * 0.40)),
            "structure": max(1, round(self.max_candidates * 0.25)),
            "semantic": max(1, round(self.max_candidates * 0.25)),
            "image": max(1, round(self.max_candidates * 0.10)),
        }
        channels = {
            "lexical": sorted(candidates, key=lambda item: item.lexical_score, reverse=True),
            "structure": sorted(candidates, key=lambda item: item.structure_score, reverse=True),
            "semantic": sorted(candidates, key=lambda item: item.semantic_score, reverse=True),
            "image": sorted(candidates, key=lambda item: item.image_score, reverse=True),
        }
        selected: list[DuplicateCandidate] = []
        seen: set[str] = set()
        for channel in ("lexical", "structure", "semantic", "image"):
            count = 0
            for candidate in channels[channel]:
                score = float(getattr(candidate, f"{channel}_score"))
                if score <= 0 or candidate.id in seen:
                    continue
                selected.append(candidate)
                seen.add(candidate.id)
                count += 1
                if count >= limits[channel] or len(selected) >= self.max_candidates:
                    break
        for candidate in candidates:
            if len(selected) >= self.max_candidates:
                break
            if candidate.id not in seen:
                selected.append(candidate)
                seen.add(candidate.id)
        selected.sort(
            key=lambda item: (
                item.rank_score,
                item.evidence_strength,
                item.similarity_score,
            ),
            reverse=True,
        )
        return selected

    def search(self, query: str = "", *, limit: int = 30) -> list[DuplicateCandidate]:
        if not query.strip():
            return self.candidates[:limit]
        query_tokens = _char_ngrams(normalize_text(query))
        ranked: list[tuple[float, DuplicateCandidate]] = []
        for candidate in self.candidates:
            haystack = normalize_text(
                f"{candidate.left.section} {candidate.left.text} "
                f"{candidate.right.section} {candidate.right.text}"
            )
            grams = _char_ngrams(haystack)
            intersection = len(query_tokens & grams)
            overlap = intersection / (min(len(query_tokens), len(grams)) or 1)
            ranked.append(
                (
                    0.35 * candidate.rank_score
                    + 0.45 * overlap
                    + 0.20 * candidate.evidence_strength,
                    candidate,
                )
            )
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in ranked[:limit]]

    def get(self, candidate_id: str) -> DuplicateCandidate | None:
        return next((c for c in self.candidates if c.id == candidate_id), None)

    def get_context(self, candidate_id: str, *, radius: int = 1) -> dict | None:
        """Return a bounded before/current/after context for both sides."""

        candidate = self.get(candidate_id)
        if candidate is None:
            return None

        def side_context(block: DocumentBlock, blocks: list[DocumentBlock]) -> dict:
            index = next((idx for idx, item in enumerate(blocks) if item.id == block.id), -1)
            if index < 0:
                return {"before": [], "current": block.to_context_dict(), "after": []}
            radius_value = max(0, min(int(radius), 3))
            return {
                "before": [
                    item.to_context_dict()
                    for item in blocks[max(0, index - radius_value) : index]
                ],
                "current": block.to_context_dict(),
                "after": [
                    item.to_context_dict()
                    for item in blocks[index + 1 : index + 1 + radius_value]
                ],
            }

        payload = candidate.to_agent_dict()
        payload["left_context"] = side_context(candidate.left, self.left_blocks)
        payload["right_context"] = side_context(candidate.right, self.right_blocks)
        return payload

    def save_cache(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "duplicate-candidates/v2",
            "algorithm_version": self.algorithm_version,
            "left_document_id": self.left_doc.id,
            "right_document_id": self.right_doc.id,
            "candidates": [candidate.to_agent_dict() for candidate in self.candidates],
            "warnings": self.warnings,
            "embedding_stats": (
                asdict(self.embedding_service.last_stats)
                if self.embedding_service is not None
                else None
            ),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
