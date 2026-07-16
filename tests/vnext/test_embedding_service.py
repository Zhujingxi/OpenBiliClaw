"""Contract tests for the OpenAI-compatible LiteLLM embedding boundary."""

from __future__ import annotations

import httpx
import pytest

from openbiliclaw.infrastructure.ai.embedding import EmbeddingService, EmbeddingSettings


def _client(handler: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, base_url="http://litellm.test/v1")


async def test_embedding_uses_only_stable_alias_and_returns_versioned_namespace() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://litellm.test/v1/embeddings"
        assert request.headers["authorization"] == "Bearer synthetic-proxy-key"
        assert request.read().decode() == ('{"input":["first","second"],"model":"obc-embedding"}')
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1, 0.2, 0.3], "index": 0},
                    {"embedding": [0.4, 0.5, 0.6], "index": 1},
                ],
                "model": "provider/deployment-must-not-shape-namespace",
            },
        )

    async with _client(httpx.MockTransport(handle)) as client:
        service = EmbeddingService(
            EmbeddingSettings(
                base_url="http://litellm.test/v1",
                api_key="synthetic-proxy-key",
                profile_version="profile-v1",
            ),
            client=client,
        )
        result = await service.embed(["first", "second"])

    assert result.vectors == ((0.1, 0.2, 0.3), (0.4, 0.5, 0.6))
    assert result.namespace.alias == "obc-embedding"
    assert result.namespace.vector_dimension == 3
    assert result.namespace.profile_version == "profile-v1"
    assert result.namespace.cache_key == "obc-embedding:3:profile-v1"


async def test_embedding_rejects_empty_input_without_http_call() -> None:
    calls = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with _client(httpx.MockTransport(handle)) as client:
        service = EmbeddingService(
            EmbeddingSettings(
                base_url="http://litellm.test/v1",
                api_key="synthetic-proxy-key",
                profile_version="profile-v1",
            ),
            client=client,
        )
        with pytest.raises(ValueError, match="at least one non-empty text"):
            await service.embed([])
        with pytest.raises(ValueError, match="at least one non-empty text"):
            await service.embed(["  "])

    assert calls == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"data": [{"index": 0, "embedding": []}]},
        {
            "data": [
                {"index": 0, "embedding": [0.1, 0.2]},
                {"index": 1, "embedding": [0.3]},
            ]
        },
        {"data": [{"index": 1, "embedding": [0.1]}]},
    ],
)
async def test_embedding_rejects_malformed_or_dimensionally_inconsistent_response(
    payload: dict[str, object],
) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _client(httpx.MockTransport(handle)) as client:
        service = EmbeddingService(
            EmbeddingSettings(
                base_url="http://litellm.test/v1",
                api_key="synthetic-proxy-key",
                profile_version="profile-v1",
            ),
            client=client,
        )
        with pytest.raises(ValueError, match="embedding response"):
            await service.embed(
                ["first", "second"] if len(payload.get("data", [])) > 1 else ["first"]
            )


async def test_embedding_does_not_retry_http_failures() -> None:
    calls = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": "rate limited by proxy"})

    async with _client(httpx.MockTransport(handle)) as client:
        service = EmbeddingService(
            EmbeddingSettings(
                base_url="http://litellm.test/v1",
                api_key="synthetic-proxy-key",
                profile_version="profile-v1",
            ),
            client=client,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await service.embed(["text"])

    assert calls == 1
