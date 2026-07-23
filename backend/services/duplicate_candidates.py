"""Deterministic A/B candidate retrieval for technical bid duplicate checks."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path
from typing import Iterable

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_NUMBER_RE = re.compile(r"(?<!\w)(?:\d+(?:\.\d+)?%?|[A-Z]{1,4}-?\d{2,})(?!\w)", re.I)
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


@dataclass(slots=True)
class DocumentDescriptor:
    id: str
    filename: str
    path: str


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

    def location(self) -> dict:
        return {
            "section": self.section,
            "start_line": self.start_line,
            "end_line": self.end_line,
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

    def to_agent_dict(self) -> dict:
        return {
            "candidate_id": self.id,
            "similarity_score": round(self.similarity_score, 4),
            "lexical_score": round(self.lexical_score, 4),
            "structure_score": round(self.structure_score, 4),
            "match_type": self.match_type,
            "left_excerpt": self.left.text,
            "left_location": self.left.location(),
            "right_excerpt": self.right.text,
            "right_location": self.right.location(),
        }


def normalize_text(text: str) -> str:
    """Normalize layout noise while preserving Chinese, letters and numbers."""
    return _PUNCT_RE.sub("", _SPACE_RE.sub("", text)).lower()


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
                    numbers=_NUMBER_RE.findall(part),
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


class DuplicateCandidateService:
    """Build and query a task-scoped A/B candidate index."""

    def __init__(
        self,
        left: DocumentDescriptor,
        right: DocumentDescriptor,
        *,
        max_candidates: int = 400,
    ):
        self.left_doc = left
        self.right_doc = right
        self.max_candidates = max_candidates
        self.left_blocks: list[DocumentBlock] = []
        self.right_blocks: list[DocumentBlock] = []
        self.candidates: list[DuplicateCandidate] = []

    async def build(self) -> list[DuplicateCandidate]:
        """Build the fully local, deterministic task-scoped candidate index."""
        self.left_blocks = parse_markdown_blocks(self.left_doc, "left")
        self.right_blocks = parse_markdown_blocks(self.right_doc, "right")
        pool = self._lexical_pool()
        self.candidates = self._finalize(pool)
        return self.candidates

    def _lexical_pool(self) -> list[dict]:
        right_grams = [_char_ngrams(b.normalized) for b in self.right_blocks]
        inverted: dict[str, list[int]] = defaultdict(list)
        for idx, grams in enumerate(right_grams):
            for gram in grams:
                inverted[gram].append(idx)

        pool: list[dict] = []
        for left in self.left_blocks:
            left_grams = _char_ngrams(left.normalized)
            votes: Counter[int] = Counter()
            for gram in left_grams:
                for idx in inverted.get(gram, ()):
                    votes[idx] += 1
            for right_idx, _ in votes.most_common(24):
                right = self.right_blocks[right_idx]
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
                if lexical < 0.16 and structure < 0.5:
                    continue
                pool.append(
                    {
                        "left": left,
                        "right": right,
                        "lexical": lexical,
                        "structure": structure,
                    }
                )
        pool.sort(key=lambda item: max(item["lexical"], item["structure"]), reverse=True)
        return pool[: self.max_candidates * 2]

    def _finalize(self, pool: list[dict]) -> list[DuplicateCandidate]:
        results: list[DuplicateCandidate] = []
        seen: set[tuple[str, str]] = set()
        for item in pool:
            left: DocumentBlock = item["left"]
            right: DocumentBlock = item["right"]
            key = (left.id, right.id)
            if key in seen:
                continue
            seen.add(key)
            lexical = float(item["lexical"])
            structure = float(item["structure"])
            score = max(lexical, 0.85 * structure)
            if left.normalized == right.normalized:
                score, match_type = 1.0, "exact"
            elif lexical >= 0.72:
                match_type = "near_exact"
            elif structure >= 0.65:
                match_type = "structural"
            else:
                match_type = "near_exact"
            if score < 0.45:
                continue
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
                )
            )
        results.sort(key=lambda c: c.similarity_score, reverse=True)
        return results[: self.max_candidates]

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
            ranked.append((0.75 * candidate.similarity_score + 0.25 * overlap, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in ranked[:limit]]

    def get(self, candidate_id: str) -> DuplicateCandidate | None:
        return next((c for c in self.candidates if c.id == candidate_id), None)

    def save_cache(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "left_document_id": self.left_doc.id,
            "right_document_id": self.right_doc.id,
            "candidates": [candidate.to_agent_dict() for candidate in self.candidates],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
