"""Cheap, deterministic duplicate-document guards.

The duplicate checker compares two bidder documents.  When both uploads (or
their parsed representations) are byte-for-byte identical there is no useful
AI work to perform, so the API and the worker can stop before candidate
retrieval and LLM calls.  Missing/temporarily unavailable files are treated as
an inconclusive check rather than turning a valid task into a server error.
"""

from __future__ import annotations

from pathlib import Path
from collections import defaultdict
from typing import Iterable

from backend.utils.file_utils import get_file_hash


def _hash_if_file(path: str | Path | None) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    try:
        if not candidate.is_file():
            return None
        return get_file_hash(candidate)
    except (OSError, ValueError):
        # A cleanup race or an inaccessible optional parse artefact should not
        # prevent the normal checker from reporting a result.
        return None


def find_identical_content_hash(
    left_original_path: str | Path | None,
    right_original_path: str | Path | None,
    left_parsed_path: str | Path | None = None,
    right_parsed_path: str | Path | None = None,
) -> tuple[str, str] | None:
    """Return ``(basis, sha256)`` when the two sides are byte-identical.

    Original uploads are checked first.  Parsed Markdown/HTML is checked as a
    fallback because old records may have lost their original upload while
    retaining the parser artefact.  A result is returned only when *both* paths
    in a pair exist and hash successfully.
    """

    pairs = (
        ("original", left_original_path, right_original_path),
        ("parsed", left_parsed_path, right_parsed_path),
    )
    for basis, left_path, right_path in pairs:
        left_hash = _hash_if_file(left_path)
        right_hash = _hash_if_file(right_path)
        if left_hash and right_hash and left_hash == right_hash:
            return basis, left_hash
    return None


def find_identical_content_groups(
    items: Iterable[tuple[str, str | Path | None, str | Path | None]],
) -> list[tuple[str, list[str], str]]:
    """Hash each batch member once and return duplicate groups.

    ``items`` contains ``(document_id, original_path, parsed_path)``.  The
    original upload is preferred; parsed artefacts are used only when an
    original is unavailable.  A group is returned as ``(basis, ids, hash)``.
    This keeps the batch guard linear in file count rather than repeatedly
    reading the same files for all N² pairs.
    """

    groups: list[tuple[str, list[str], str]] = []
    original_sets: set[tuple[str, ...]] = set()
    for basis, index in (
        ("original", 1),
        ("parsed", 2),
    ):
        by_hash: dict[str, list[str]] = defaultdict(list)
        for document_id, original_path, parsed_path in items:
            path = original_path if basis == "original" else parsed_path
            digest = _hash_if_file(path)
            if digest:
                by_hash[digest].append(str(document_id))
        for digest, document_ids in by_hash.items():
            if len(document_ids) > 1:
                ids = tuple(sorted(document_ids))
                if basis == "original":
                    original_sets.add(ids)
                elif ids in original_sets:
                    continue
                groups.append((basis, list(ids), digest))
        # If an original group exists for a set of documents, do not emit the
        # same set again from parsed files.
    return groups


__all__ = ["find_identical_content_hash", "find_identical_content_groups"]
