"""Dimension-safe deterministic embedding batching."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from openbiliclaw.ai.providers.embeddings.protocol import (
    EmbeddingBatch,
    EmbeddingModelInfo,
    EmbeddingResult,
    EmbeddingTransport,
    EmbeddingTransportError,
    EmbeddingUsage,
    Vector,
)

if TYPE_CHECKING:
    from openbiliclaw.core.resources import ResourceBudget


BGE_SMALL_ZH_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


def query_prefix_for_model(model_name: str) -> str:
    """Return the model-card retrieval instruction applied to queries only."""

    return BGE_SMALL_ZH_QUERY_PREFIX if model_name.casefold().endswith("bge-small-zh-v1.5") else ""


class EmbeddingService:
    """Keep embedding execution separate from chat model routing."""

    def __init__(
        self,
        transport: EmbeddingTransport,
        model: EmbeddingModelInfo,
        budget: ResourceBudget,
        *,
        batch_size: int = 32,
        retries: int = 1,
        timeout_seconds: float = 30,
        query_prefix: str = "",
    ) -> None:
        if batch_size < 1 or retries < 0 or timeout_seconds <= 0:
            raise ValueError("embedding execution limits must be positive")
        self._transport = transport
        self._model = model
        self._budget = budget
        self._batch_size = batch_size
        self._retries = retries
        self._timeout = timeout_seconds
        self._query_prefix = query_prefix

    async def embed_documents(self, texts: tuple[str, ...]) -> EmbeddingResult:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("embedding input must not be empty")
        batches = tuple(
            texts[index : index + self._batch_size]
            for index in range(0, len(texts), self._batch_size)
        )
        # gather preserves input order while ResourceBudget bounds transport concurrency.
        results = await asyncio.gather(*(self._embed_batch(batch) for batch in batches))
        vectors: list[Vector] = []
        input_tokens = 0
        for expected, result in zip(batches, results, strict=True):
            if len(result.vectors) != len(expected):
                raise ValueError("embedding provider returned an unexpected vector count")
            for vector in result.vectors:
                if len(vector) != self._model.dimensions:
                    raise ValueError("embedding vector dimension mismatch")
            vectors.extend(result.vectors)
            input_tokens += result.input_tokens
        return EmbeddingResult(
            vectors=tuple(vectors),
            usage=EmbeddingUsage(requests=len(batches), input_tokens=input_tokens),
            model=self._model,
        )

    async def embed_query(self, text: str) -> Vector:
        result = await self.embed_documents((self._query_prefix + text,))
        return result.vectors[0]

    async def _embed_batch(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        async with self._budget.acquire():
            for attempt in range(self._retries + 1):
                try:
                    async with asyncio.timeout(self._timeout):
                        return await self._transport.embed(texts)
                except EmbeddingTransportError as exc:
                    if not exc.retryable or attempt == self._retries:
                        raise
        raise RuntimeError("unreachable embedding retry state")
