"""Embedding service for semantic similarity using Mini-Max API."""

import logging
import math
from typing import Literal

from openai import AsyncOpenAI

from backend.config import get_settings

logger = logging.getLogger(__name__)

# Severity ordering for merge priority
SEVERITY_ORDER: dict[str, int] = {
    "critical": 3,
    "major": 2,
    "minor": 1,
}


class EmbeddingService:
    """Service for computing text embeddings via Mini-Max API."""

    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(
            api_key=settings.mini_agent_api_key,
            base_url=settings.mini_agent_api_base,
        )
        self.model = "embeddings"  # MiniMax embedding model

    async def get_embedding(self, text: str) -> list[float]:
        """Get embedding vector for a single text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Embedding API error: {e}")
            raise

    async def compute_similarity(self, text1: str, text2: str) -> float:
        """Compute cosine similarity between two texts.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score between 0.0 and 1.0
        """
        embedding1 = await self.get_embedding(text1)
        embedding2 = await self.get_embedding(text2)

        # Cosine similarity
        dot_product = sum(a * b for a, b in zip(embedding1, embedding2))
        magnitude1 = math.sqrt(sum(a * a for a in embedding1))
        magnitude2 = math.sqrt(sum(b * b for b in embedding2))

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)

    def merge_candidates(
        self,
        existing: dict,
        new: dict,
        similarity_threshold: float = 0.85,
    ) -> tuple[dict | None, bool]:
        """Determine if new result should merge with existing.

        Compares requirement_content + bid_content + explanation + suggestion
        and returns the better record based on severity.

        Args:
            existing: Existing ProjectReviewResult dict
            new: New ReviewResult dict
            similarity_threshold: Minimum similarity to consider as duplicate

        Returns:
            Tuple of (merged_record, is_duplicate)
            - merged_record: The record to store (existing if severity higher, else new)
            - is_duplicate: True if texts are semantically similar
        """
        # Build comparison text
        existing_text = self._build_comparison_text(existing)
        new_text = self._build_comparison_text(new)

        # Compute similarity synchronously (will be called from async context)
        import asyncio
        loop = asyncio.get_event_loop()
        similarity = loop.run_until_complete(self.compute_similarity(existing_text, new_text))

        if similarity >= similarity_threshold:
            # Determine which to keep based on severity
            existing_severity_rank = SEVERITY_ORDER.get(existing.get("severity", "minor"), 0)
            new_severity_rank = SEVERITY_ORDER.get(new.get("severity", "minor"), 0)

            if new_severity_rank >= existing_severity_rank:
                return new, True
            else:
                return existing, True

        return None, False

    def _build_comparison_text(self, record: dict) -> str:
        """Build text for similarity comparison from record fields."""
        parts = []
        for field in ["requirement_content", "bid_content", "explanation", "suggestion"]:
            if record.get(field):
                parts.append(str(record[field]))
        return " ".join(parts)
