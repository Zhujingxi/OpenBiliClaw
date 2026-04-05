"""Embedding service with caching for semantic similarity.

Provides text embedding via Gemini's text-embedding-004 model,
with in-memory caching to avoid redundant API calls within a session.
Used by the discovery engine for semantic topic deduplication.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SupportsEmbed(Protocol):
    """Protocol for providers that support text embedding."""

    async def embed(self, text: str, *, model: str = ...) -> list[float]: ...


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors (pure Python)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingService:
    """Cached embedding service for semantic similarity operations.

    Wraps a provider that supports ``embed()`` and adds:
    - In-memory LRU-style cache (keyed by normalized text)
    - Batch embedding with concurrency control
    - Semantic similarity comparison with configurable threshold

    All parameters (model, threshold, cache_size) can be configured
    via ``[llm.embedding]`` in config.toml.
    """

    def __init__(
        self,
        provider: SupportsEmbed,
        *,
        model: str = "gemini-embedding-001",
        cache_size: int = 500,
        similarity_threshold: float = 0.82,
    ) -> None:
        self._provider = provider
        self._model = model
        self._cache: dict[str, list[float]] = {}
        self._cache_size = cache_size
        self.similarity_threshold = similarity_threshold

    async def embed(self, text: str) -> list[float]:
        """Get embedding for text, using cache when available."""
        key = text.strip().lower()[:200]
        if not key:
            return []
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            vector = await self._provider.embed(key, model=self._model)
        except Exception:
            logger.warning("Embedding failed for: %s", key[:50], exc_info=True)
            return []
        # Evict oldest if cache full
        if len(self._cache) >= self._cache_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        self._cache[key] = vector
        return vector

    async def are_similar(self, text_a: str, text_b: str) -> bool:
        """Check if two texts are semantically similar above threshold."""
        vec_a = await self.embed(text_a)
        vec_b = await self.embed(text_b)
        if not vec_a or not vec_b:
            return False
        return cosine_similarity(vec_a, vec_b) >= self.similarity_threshold

    async def find_similar_cluster(
        self,
        text: str,
        existing_clusters: dict[str, list[float]],
    ) -> str | None:
        """Find which existing cluster a text belongs to, or None if novel.

        Args:
            text: The text to classify.
            existing_clusters: Map of cluster_label → centroid_vector.

        Returns:
            The label of the most similar cluster (if above threshold), or None.
        """
        vec = await self.embed(text)
        if not vec:
            return None
        best_label: str | None = None
        best_sim = 0.0
        for label, centroid in existing_clusters.items():
            sim = cosine_similarity(vec, centroid)
            if sim > best_sim:
                best_sim = sim
                best_label = label
        if best_sim >= self.similarity_threshold:
            return best_label
        return None

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self._cache.clear()
