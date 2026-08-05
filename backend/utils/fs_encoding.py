"""Filesystem filename encoding helpers.

Rule documents under ``docs/rules/`` are git-ignored and deployed to servers by
manual copy (scp/zip). On Chinese Windows the deploy tooling sometimes writes
the filename *bytes* as GBK/GB18030 instead of UTF-8. On Linux, ``pathlib`` and
``os.listdir`` then decode those bytes via the ``surrogateescape`` error
handler, yielding strings full of lone surrogates (``U+DC80..U+DCFF``). Those
surrogates cannot be re-encoded as UTF-8, so feeding such a name into
asyncpg/JSON raises ``DataError`` and kills the whole "start check" flow.

These helpers detect that situation and recover the real (UTF-8) filename by
re-decoding the raw bytes as GB18030 (PRC national standard, a superset of
GBK/CP936 — the usual culprit for names coming off a Chinese Windows deploy).
``heal_path``/``heal_directory`` additionally rename the file on disk to the
UTF-8 name, so downstream code that reads the path back from the DB can still
open the file. Already-clean UTF-8 names are left untouched, so both encodings
are supported and the heal is idempotent / safe to run on every scan.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def has_surrogates(value: str) -> bool:
    """Return True if *value* contains lone surrogateescape surrogates."""
    return any("\udc80" <= ch <= "\udcff" for ch in value)


def decode_fs_name(name: str) -> str:
    """Re-decode a filesystem-derived string into a clean UTF-8 string.

    No-op for already-clean UTF-8. For strings carrying surrogateescape
    surrogates, the raw on-disk bytes are recovered and re-decoded as
    GB18030, falling back to ``replace`` so lone surrogates can never leak out
    (e.g. into a DB insert).
    """
    if not has_surrogates(name):
        return name
    raw = name.encode("utf-8", "surrogateescape")
    try:
        return raw.decode("gb18030")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


def heal_path(path: Path) -> Path:
    """Rename *path*'s file to its recovered UTF-8 name; return the new Path.

    Returns *path* unchanged when the name is already clean UTF-8 or when the
    rename fails (permission/collision). In the failure case callers should
    still run the name through :func:`decode_fs_name` before storing it, so the
    DB insert won't crash even if the on-disk name stays non-UTF-8.
    """
    name = path.name
    if not has_surrogates(name):
        return path
    clean_name = decode_fs_name(name)
    if clean_name == name:
        return path
    target = path.with_name(clean_name)
    if target.exists():
        # Refuse to clobber an existing UTF-8 file (e.g. a partial previous heal).
        logger.warning("skip heal %r -> %r: target already exists", name, clean_name)
        return path
    try:
        os.rename(path, target)
    except OSError as exc:
        logger.warning("heal rename failed %r -> %r: %s", name, clean_name, exc)
        return path
    logger.info("healed rule filename %r -> %r", name, clean_name)
    return target


def heal_directory(directory: Path, pattern: str = "*.md") -> None:
    """Rename every file under *directory* matching *pattern* to UTF-8 names.

    Files whose names are already valid UTF-8 are left untouched. Idempotent
    and safe to run on every scan.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return
    for path in directory.glob(pattern):
        heal_path(path)
