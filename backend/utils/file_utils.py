"""File utility functions."""

import hashlib
from pathlib import Path


def get_file_hash(file_path: Path) -> str:
    """Calculate SHA256 hash of a file.

    Args:
        file_path: Path to the file

    Returns:
        Hex-encoded SHA256 hash
    """
    sha256_hash = hashlib.sha256()
    with file_path.open("rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def get_file_extension(filename: str) -> str:
    """Get the file extension from a filename.

    Args:
        filename: The filename

    Returns:
        The extension without the dot (e.g., 'pdf', 'docx')
    """
    return Path(filename).suffix.lstrip(".").lower()


def is_supported_document(filename: str) -> bool:
    """Check if a file is a supported document type.

    Args:
        filename: The filename to check

    Returns:
        True if supported, False otherwise
    """
    supported = {"pdf", "docx", "doc"}
    return get_file_extension(filename) in supported
