"""Structured table comparison channel for duplicate checks."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Iterable

from backend.schemas.document_artifacts import DuplicateEvidenceBlock
from backend.services.document_artifacts import normalize_text


def _cells(block: DuplicateEvidenceBlock) -> list[str]:
    return [value.strip() for value in re.split(r"\s*\|\s*", block.raw_text) if value.strip()]


def _similarity(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    a_set, b_set = set(a), set(b)
    jaccard = len(a_set & b_set) / (len(a_set | b_set) or 1)
    return 0.55 * SequenceMatcher(None, a, b).ratio() + 0.45 * jaccard


def _header_values(block: DuplicateEvidenceBlock) -> list[str]:
    if not block.header_map:
        return []
    return [block.header_map[key] for key in sorted(block.header_map, key=lambda value: int(value))]


@dataclass(slots=True)
class TableComparison:
    id: str
    left: DuplicateEvidenceBlock
    right: DuplicateEvidenceBlock
    header_similarity: float
    row_alignment_score: float
    numeric_signature_score: float
    rare_cell_overlap: float
    table_structure_score: float
    score: float
    shared_rare_cells: list[str]

    def to_dict(self) -> dict:
        return {
            "table_candidate_id": self.id,
            "score": round(self.score, 4),
            "header_similarity": round(self.header_similarity, 4),
            "row_alignment_score": round(self.row_alignment_score, 4),
            "numeric_signature_score": round(self.numeric_signature_score, 4),
            "rare_cell_overlap": round(self.rare_cell_overlap, 4),
            "table_structure_score": round(self.table_structure_score, 4),
            "shared_rare_cells": self.shared_rare_cells,
            "left": {
                "block_id": self.left.block_id,
                "document_id": self.left.document_id,
                "table_id": self.left.table_id,
                "row_index": self.left.row_index,
                "section_path": self.left.section_path,
                "page_number": self.left.page_number,
                "start_line": self.left.start_line,
                "header_map": self.left.header_map,
                "cells": _cells(self.left),
                "raw_text": self.left.raw_text,
            },
            "right": {
                "block_id": self.right.block_id,
                "document_id": self.right.document_id,
                "table_id": self.right.table_id,
                "row_index": self.right.row_index,
                "section_path": self.right.section_path,
                "page_number": self.right.page_number,
                "start_line": self.right.start_line,
                "header_map": self.right.header_map,
                "cells": _cells(self.right),
                "raw_text": self.right.raw_text,
            },
        }


def compare_table_blocks(
    left_blocks: Iterable[DuplicateEvidenceBlock],
    right_blocks: Iterable[DuplicateEvidenceBlock],
    *,
    limit: int = 100,
) -> list[TableComparison]:
    """Compare table rows using structure-specific, independently visible scores."""

    left_rows = [block for block in left_blocks if block.content_type == "table_row"]
    right_rows = [block for block in right_blocks if block.content_type == "table_row"]
    all_cells = [normalize_text(cell) for block in left_rows + right_rows for cell in _cells(block)]
    frequencies = Counter(cell for cell in all_cells if cell)
    results: list[TableComparison] = []

    for left in left_rows:
        left_cells = _cells(left)
        left_headers = _header_values(left)
        for right in right_rows:
            right_cells = _cells(right)
            right_headers = _header_values(right)
            header_similarity = _similarity(" | ".join(left_headers), " | ".join(right_headers))
            primary_similarity = _similarity(
                left_cells[0] if left_cells else "",
                right_cells[0] if right_cells else "",
            )
            index_distance = abs((left.row_index or 0) - (right.row_index or 0))
            row_alignment = 0.7 * primary_similarity + 0.3 * (1.0 / (1.0 + index_distance))

            left_signature = set(left.numbers + left.models + left.units + left.identifiers)
            right_signature = set(right.numbers + right.models + right.units + right.identifiers)
            numeric_signature = (
                len(left_signature & right_signature) / (len(left_signature | right_signature) or 1)
                if left_signature or right_signature
                else 0.0
            )
            left_norm_cells = {normalize_text(cell) for cell in left_cells if normalize_text(cell)}
            right_norm_cells = {normalize_text(cell) for cell in right_cells if normalize_text(cell)}
            shared_rare = sorted(
                cell
                for cell in left_norm_cells & right_norm_cells
                if frequencies[cell] <= 2 and len(cell) >= 3
            )
            rare_overlap = min(1.0, len(shared_rare) / max(1, min(len(left_cells), len(right_cells))))
            column_ratio = min(len(left_cells), len(right_cells)) / max(1, max(len(left_cells), len(right_cells)))
            structure = 0.65 * column_ratio + 0.35 * header_similarity
            score = min(
                1.0,
                0.24 * header_similarity
                + 0.20 * row_alignment
                + 0.26 * numeric_signature
                + 0.20 * rare_overlap
                + 0.10 * structure,
            )
            if score < 0.20 and numeric_signature < 0.50 and not shared_rare:
                continue
            digest = sha256(f"{left.block_id}\0{right.block_id}".encode()).hexdigest()[:20]
            results.append(
                TableComparison(
                    id=digest,
                    left=left,
                    right=right,
                    header_similarity=header_similarity,
                    row_alignment_score=row_alignment,
                    numeric_signature_score=numeric_signature,
                    rare_cell_overlap=rare_overlap,
                    table_structure_score=structure,
                    score=score,
                    shared_rare_cells=shared_rare,
                )
            )

    results.sort(
        key=lambda item: (
            item.score,
            item.rare_cell_overlap,
            item.numeric_signature_score,
        ),
        reverse=True,
    )
    return results[: max(1, int(limit))]


__all__ = ["TableComparison", "compare_table_blocks"]
