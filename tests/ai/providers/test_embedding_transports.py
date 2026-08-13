from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Callable

import httpx
import pytest
from openai import APIConnectionError, AsyncOpenAI
from openai.types import CreateEmbeddingResponse
from openai.types.create_embedding_response import Usage
from openai.types.embedding import Embedding

from openbiliclaw.ai.providers.embeddings.protocol import EmbeddingTransportError
from openbiliclaw.ai.providers.embeddings.providers import (
    NativeEmbeddingTransport,
    build_embedding_transport,
)
from openbiliclaw.ai.providers.models import ModelInstanceConfig
from openbiliclaw.ai.providers.verification import UnsupportedCapabilityError

T = TypeVar("T")


class FakeVault:
    def resolve(self, secret_id: str, callback: Callable[[memoryview], T]) -> T:
        assert secret_id == "cred_" + "a" * 32
        return callback(memoryview(b"canary-key"))


def config(provider: str = "openai", *, endpoint: str | None = None) -> ModelInstanceConfig:
    return ModelInstanceConfig(
        provider=provider,
        protocol="openai" if provider == "openai" else provider,
        model_name="embedding-model",
        endpoint=endpoint,
        secret_ref="cred_" + "a" * 32,
    )


@dataclass
class FakeEmbeddings:
    calls: list[dict[str, object]]
    error: Exception | None = None

    async def create(self, **kwargs: object) -> CreateEmbeddingResponse:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return CreateEmbeddingResponse(
            data=[Embedding(embedding=[1.0, 2.0], index=0, object="embedding")],
            model="embedding-model",
            object="list",
            usage=Usage(prompt_tokens=3, total_tokens=3),
        )


class FakeClient:
    def __init__(self, embeddings: FakeEmbeddings) -> None:
        self.embeddings = embeddings


async def test_official_openai_embedding_requests_output_dimensions() -> None:
    calls: list[dict[str, object]] = []
    transport = NativeEmbeddingTransport(
        cast("AsyncOpenAI", FakeClient(FakeEmbeddings(calls))),
        config(),
        output_dimensions=2,
    )
    batch = await transport.embed(("hello",))
    assert calls == [{"model": "embedding-model", "input": ("hello",), "dimensions": 2}]
    assert batch.vectors == ((1.0, 2.0),)
    assert batch.input_tokens == 3


async def test_custom_endpoint_embedding_omits_openai_dimensions_parameter() -> None:
    calls: list[dict[str, object]] = []
    transport = NativeEmbeddingTransport(
        cast("AsyncOpenAI", FakeClient(FakeEmbeddings(calls))),
        config(endpoint="https://provider.invalid/v1"),
        output_dimensions=2,
    )
    await transport.embed(("hello",))
    assert calls == [{"model": "embedding-model", "input": ("hello",)}]


def test_builder_resolves_secret_and_returns_native_openai_transport() -> None:
    assert isinstance(
        build_embedding_transport(config(), FakeVault(), output_dimensions=2),
        NativeEmbeddingTransport,
    )


@pytest.mark.parametrize("provider", ["anthropic", "google", "openrouter"])
def test_provider_without_native_embeddings_fails_closed(provider: str) -> None:
    with pytest.raises(UnsupportedCapabilityError, match=f"{provider} embeddings"):
        build_embedding_transport(config(provider), FakeVault(), output_dimensions=2)


async def test_native_embedding_errors_are_typed() -> None:
    error = APIConnectionError(request=httpx.Request("POST", "https://provider.invalid"))
    transport = NativeEmbeddingTransport(
        cast("AsyncOpenAI", FakeClient(FakeEmbeddings([], error))),
        config(),
        output_dimensions=2,
    )
    with pytest.raises(EmbeddingTransportError) as raised:
        await transport.embed(("hello",))
    assert raised.value.retryable
