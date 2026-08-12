from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from openbiliclaw.ai.providers.embeddings.protocol import (
    EmbeddingBatch,
    EmbeddingModelInfo,
    EmbeddingTransportError,
)
from openbiliclaw.ai.providers.embeddings.service import EmbeddingService
from openbiliclaw.core.resources import ResourceBudget


@dataclass
class FakeTransport:
    dimensions: int = 2
    calls: list[tuple[str, ...]] = field(default_factory=list)
    failures: int = 0

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        self.calls.append(texts)
        if self.failures:
            self.failures -= 1
            raise EmbeddingTransportError(retryable=True)
        return EmbeddingBatch(
            vectors=tuple((float(len(x)), 1.0) for x in texts), input_tokens=len(texts)
        )


def info(dimensions: int = 2) -> EmbeddingModelInfo:
    return EmbeddingModelInfo(
        provider="openai",
        model="embedding-model",
        dimensions=dimensions,
        normalized=True,
        version="1",
    )


async def test_documents_are_batched_deterministically_and_usage_is_summed() -> None:
    transport = FakeTransport()
    service = EmbeddingService(transport, info(), ResourceBudget("embedding", 2), batch_size=2)
    result = await service.embed_documents(("a", "bb", "ccc", "dddd", "eeeee"))
    assert transport.calls == [("a", "bb"), ("ccc", "dddd"), ("eeeee",)]
    assert result.vectors == ((1.0, 1.0), (2.0, 1.0), (3.0, 1.0), (4.0, 1.0), (5.0, 1.0))
    assert result.usage.requests == 3
    assert result.usage.input_tokens == 5
    assert result.model == info()


async def test_query_requires_one_nonempty_input() -> None:
    service = EmbeddingService(FakeTransport(), info(), ResourceBudget("embedding", 1))
    assert await service.embed_query("hello") == (5.0, 1.0)
    with pytest.raises(ValueError, match="empty"):
        await service.embed_query("  ")
    with pytest.raises(ValueError, match="empty"):
        await service.embed_documents(())


async def test_dimension_and_batch_count_are_validated() -> None:
    service = EmbeddingService(FakeTransport(dimensions=2), info(3), ResourceBudget("embedding", 1))
    with pytest.raises(ValueError, match="dimension"):
        await service.embed_documents(("a",))

    class ShortTransport(FakeTransport):
        async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
            return EmbeddingBatch(vectors=(), input_tokens=0)

    service = EmbeddingService(ShortTransport(), info(), ResourceBudget("embedding", 1))
    with pytest.raises(ValueError, match="count"):
        await service.embed_documents(("a",))


async def test_retry_timeout_and_cancellation() -> None:
    transport = FakeTransport(failures=1)
    service = EmbeddingService(transport, info(), ResourceBudget("embedding", 1), retries=1)
    assert await service.embed_query("a") == (1.0, 1.0)
    assert len(transport.calls) == 2

    class HangingTransport(FakeTransport):
        async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    timed = EmbeddingService(
        HangingTransport(), info(), ResourceBudget("embedding", 1), timeout_seconds=0.001
    )
    with pytest.raises(TimeoutError):
        await timed.embed_query("a")

    task = asyncio.create_task(
        EmbeddingService(HangingTransport(), info(), ResourceBudget("embedding", 1)).embed_query(
            "a"
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_model_identity_cache_key_has_complete_provenance() -> None:
    model = info()
    assert model.cache_key("hello") != model.cache_key("world")
    assert info(3).identity != model.identity
    with pytest.raises(ValueError):
        EmbeddingModelInfo(
            provider="openai",
            model="m",
            dimensions=0,
            normalized=False,
            version="1",
        )
