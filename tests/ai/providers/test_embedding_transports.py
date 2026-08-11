from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

import httpx
import pytest

from openbiliclaw.ai.providers.embeddings.protocol import EmbeddingTransportError
from openbiliclaw.ai.providers.embeddings.providers import (
    EmbeddingProviderKind,
    EmbeddingTransportConfig,
    GoogleEmbeddingTransport,
    OllamaEmbeddingTransport,
    OpenAIEmbeddingTransport,
    build_embedding_transport,
)

T = TypeVar("T")


class FakeVault:
    def resolve(self, secret_id: str, callback: Callable[[memoryview], T]) -> T:
        assert secret_id.startswith("cred_")
        return callback(memoryview(b"canary-key"))


@pytest.mark.parametrize(
    ("provider", "response", "transport_type", "tokens"),
    [
        (
            EmbeddingProviderKind.OPENAI,
            {"data": [{"embedding": [1, 2]}], "usage": {"prompt_tokens": 3}},
            OpenAIEmbeddingTransport,
            3,
        ),
        (
            EmbeddingProviderKind.GOOGLE,
            {"embeddings": [{"values": [1, 2]}], "usageMetadata": {"promptTokenCount": 4}},
            GoogleEmbeddingTransport,
            4,
        ),
        (
            EmbeddingProviderKind.OLLAMA,
            {"embeddings": [[1, 2]], "prompt_eval_count": 5},
            OllamaEmbeddingTransport,
            5,
        ),
    ],
)
async def test_configured_transports_parse_typed_responses(
    provider: EmbeddingProviderKind,
    response: dict[str, object],
    transport_type: type[OpenAIEmbeddingTransport]
    | type[GoogleEmbeddingTransport]
    | type[OllamaEmbeddingTransport],
    tokens: int,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert b'"model"' in request.content
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        config = EmbeddingTransportConfig(
            provider=provider,
            model="m",
            endpoint="https://provider.invalid/embed",
            secret_ref=None if provider is EmbeddingProviderKind.OLLAMA else "cred_" + "a" * 32,
            output_dimensions=2,
        )
        transport = build_embedding_transport(config, FakeVault(), client)
        assert isinstance(transport, transport_type)
        batch = await transport.embed(("hello",))
        assert batch.vectors == ((1.0, 2.0),)
        assert batch.input_tokens == tokens
        if provider is EmbeddingProviderKind.OPENAI:
            assert client.headers["authorization"] == "Bearer canary-key"
        if provider is EmbeddingProviderKind.GOOGLE:
            assert client.headers["x-goog-api-key"] == "canary-key"


async def test_transport_classifies_status_without_leaking_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="secret upstream body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        config = EmbeddingTransportConfig(
            provider=EmbeddingProviderKind.OLLAMA,
            model="m",
            output_dimensions=2,
        )
        with pytest.raises(EmbeddingTransportError, match="provider failed") as caught:
            await OllamaEmbeddingTransport(client, config).embed(("hello",))
        assert caught.value.retryable
        assert "secret" not in str(caught.value)


def test_remote_embedding_transport_requires_secret() -> None:
    config = EmbeddingTransportConfig(
        provider=EmbeddingProviderKind.OPENAI,
        model="m",
        output_dimensions=2,
    )
    with pytest.raises(ValueError, match="credential"):
        build_embedding_transport(config, FakeVault(), httpx.AsyncClient())
