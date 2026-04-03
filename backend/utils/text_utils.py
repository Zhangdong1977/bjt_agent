"""Text processing utility functions."""

import re
from typing import Any


def truncate_text(text: str, max_length: int = 1000, suffix: str = "...") -> str:
    """Truncate text to a maximum length.

    Args:
        text: The text to truncate
        max_length: Maximum length before truncation
        suffix: Suffix to append when truncating

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def clean_text(text: str) -> str:
    """Clean text by removing extra whitespace.

    Args:
        text: The text to clean

    Returns:
        Cleaned text
    """
    # Replace multiple whitespace with single space
    text = re.sub(r"\s+", " ", text)
    # Strip leading/trailing whitespace
    return text.strip()


def extract_keywords(text: str, max_keywords: int = 10) -> list[str]:
    """Extract keywords from text.

    Args:
        text: The text to extract keywords from
        max_keywords: Maximum number of keywords to return

    Returns:
        List of keywords
    """
    # Simple keyword extraction based on word frequency
    words = re.findall(r"\b[a-zA-Z\u4e00-\u9fff]{2,}\b", text.lower())

    # Count word frequencies
    word_freq: dict[str, int] = {}
    for word in words:
        word_freq[word] = word_freq.get(word, 0) + 1

    # Sort by frequency and return top keywords
    sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
    return [word for word, _ in sorted_words[:max_keywords]]


def normalize_line_endings(text: str) -> str:
    """Normalize line endings to Unix style (LF).

    Args:
        text: The text to normalize

    Returns:
        Text with normalized line endings
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_ai_think_tags(text: str) -> str:
    """Remove AI think tags and their content from text.

    Handles both the real AI think tag format (<think>和</think>) and the
    angled bracket format (<thought></thought>) used in some tests.

    Args:
        text: The text containing AI think tags

    Returns:
        Text with think tags and their content removed
    """
    # Match <think>...</think> pattern (real AI think tags, including multiline)
    # Using [\s\S]*? for non-greedy matching across newlines
    text = re.sub(r'<think>[\s\S]*?</think>', '', text)
    # Also match <thought>...</thought> format
    text = re.sub(r'<thought>[\s\S]*?</thought>', '', text)
    return text.strip()
