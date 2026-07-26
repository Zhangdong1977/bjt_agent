"""Task-scoped, immutable source retrieval for duplicate checks.

Tender and public-reference documents are indexed separately from bid
documents.  Every returned match carries a concrete document id, block id,
snapshot hash and version; callers therefore cannot turn model memory or an
uncited web summary into formal source evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from backend.schemas.document_artifacts import DuplicateEvidenceBlock
from backend.services.document_artifacts import load_evidence_blocks, normalize_text


@dataclass(slots=True)
class SourceDocumentDescriptor:
    id: str
    filename: str
    evidence_blocks_path: str | None
    source_basis: str
    snapshot_hash: str | None
    version: str | None
    source_uri: str | None = None

    def __post_init__(self) -> None:
        if self.source_basis not in {"tender", "public"}:
            raise ValueError("source_basis must be tender or public")


@dataclass(slots=True)
class DuplicateSourceMatch:
    id: str
    document: SourceDocumentDescriptor
    block: DuplicateEvidenceBlock
    score: float

    def to_agent_dict(self, *, max_chars: int = 1200) -> dict:
        return {
            "source_reference_id": self.id,
            "source_basis": self.document.source_basis,
            "source_document_id": self.document.id,
            "source_filename": self.document.filename,
            "source_block_id": self.block.block_id,
            "source_excerpt": self.block.raw_text[:max_chars],
            "source_location": {
                "section_path": self.block.section_path,
                "page_number": self.block.page_number,
                "bbox": self.block.bbox,
                "start_line": self.block.start_line,
                "end_line": self.block.end_line,
                "content_type": self.block.content_type,
                "table_id": self.block.table_id,
                "row_index": self.block.row_index,
            },
            "source_snapshot_hash": self.document.snapshot_hash,
            "source_version": self.document.version,
            "source_uri": self.document.source_uri,
            "retrieval_score": round(self.score, 4),
        }


def _char_ngrams(value: str, size: int = 2) -> set[str]:
    compact = normalize_text(value).replace(" ", "")
    if not compact:
        return set()
    if len(compact) <= size:
        return {compact}
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


class DuplicateSourceIndex:
    """A bounded in-memory index over task-attached source snapshots."""

    def __init__(self, documents: Iterable[SourceDocumentDescriptor]):
        self.documents = list(documents)
        self.blocks: list[tuple[SourceDocumentDescriptor, DuplicateEvidenceBlock]] = []
        self.matches: dict[str, DuplicateSourceMatch] = {}
        self.warnings: list[str] = []

    async def build(self) -> int:
        self.blocks = []
        self.matches = {}
        self.warnings = []
        for document in self.documents:
            if not document.snapshot_hash or not document.version:
                self.warnings.append(f"source_snapshot_incomplete:{document.id}")
                continue
            path = Path(document.evidence_blocks_path) if document.evidence_blocks_path else None
            if path is None or not path.is_file():
                self.warnings.append(f"source_evidence_unavailable:{document.id}")
                continue
            for block in load_evidence_blocks(path):
                if block.content_type not in {
                    "heading",
                    "paragraph",
                    "table",
                    "table_row",
                    "image_ocr",
                    "caption",
                }:
                    continue
                if len(block.normalized_text.replace(" ", "")) < 4:
                    continue
                # The descriptor, not parser/model output, is authoritative.
                block = block.model_copy(update={"source_basis": document.source_basis})
                self.blocks.append((document, block))
        return len(self.blocks)

    def search(
        self,
        query: str,
        *,
        source_basis: str | None = None,
        limit: int = 12,
    ) -> list[DuplicateSourceMatch]:
        if source_basis not in {None, "tender", "public"}:
            return []
        query_normalized = normalize_text(query)
        query_grams = _char_ngrams(query_normalized)
        if not query_grams:
            return []

        ranked: list[DuplicateSourceMatch] = []
        for document, block in self.blocks:
            if source_basis and document.source_basis != source_basis:
                continue
            block_grams = _char_ngrams(block.normalized_text)
            if not block_grams:
                continue
            intersection = len(query_grams & block_grams)
            containment = intersection / (min(len(query_grams), len(block_grams)) or 1)
            union = len(query_grams | block_grams) or 1
            jaccard = intersection / union
            sequence = SequenceMatcher(
                None,
                query_normalized[:2000],
                block.normalized_text[:2000],
            ).ratio()
            identifier_overlap = len(
                set(block.identifiers + block.models + block.numbers)
                & set(query_normalized.upper().split())
            )
            score = min(
                1.0,
                0.48 * containment
                + 0.32 * jaccard
                + 0.20 * sequence
                + min(0.12, 0.04 * identifier_overlap),
            )
            if score < 0.08:
                continue
            digest = sha256(
                f"{document.id}\0{block.block_id}\0{document.snapshot_hash}".encode()
            ).hexdigest()[:20]
            ranked.append(
                DuplicateSourceMatch(
                    id=digest,
                    document=document,
                    block=block,
                    score=score,
                )
            )

        ranked.sort(key=lambda item: item.score, reverse=True)
        result = ranked[: max(1, min(int(limit), 50))]
        self.matches.update({item.id: item for item in result})
        return result

    def get(self, source_reference_id: str) -> DuplicateSourceMatch | None:
        cached = self.matches.get(source_reference_id)
        if cached is not None:
            return cached
        for document, block in self.blocks:
            digest = sha256(
                f"{document.id}\0{block.block_id}\0{document.snapshot_hash}".encode()
            ).hexdigest()[:20]
            if digest == source_reference_id:
                match = DuplicateSourceMatch(
                    id=digest,
                    document=document,
                    block=block,
                    score=1.0,
                )
                self.matches[digest] = match
                return match
        return None

    def get_context(self, source_reference_id: str, *, radius: int = 1) -> dict | None:
        match = self.get(source_reference_id)
        if match is None:
            return None
        document_blocks = [
            block for document, block in self.blocks if document.id == match.document.id
        ]
        index = next(
            (i for i, block in enumerate(document_blocks) if block.block_id == match.block.block_id),
            -1,
        )
        radius = max(0, min(int(radius), 3))

        def brief(block: DuplicateEvidenceBlock) -> dict:
            return {
                "block_id": block.block_id,
                "text": block.raw_text[:1000],
                "section_path": block.section_path,
                "page_number": block.page_number,
                "start_line": block.start_line,
                "end_line": block.end_line,
                "content_type": block.content_type,
            }

        payload = match.to_agent_dict()
        payload["context"] = {
            "before": [brief(block) for block in document_blocks[max(0, index - radius) : index]],
            "current": brief(match.block),
            "after": [
                brief(block)
                for block in document_blocks[index + 1 : index + 1 + radius]
            ],
        }
        return payload


__all__ = [
    "DuplicateSourceIndex",
    "DuplicateSourceMatch",
    "SourceDocumentDescriptor",
]
