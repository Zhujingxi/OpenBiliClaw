"""Tests for DashScope multimodal embedding provider (Qwen)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openbiliclaw.llm.base import LLMProviderError, LLMResponseError
from openbiliclaw.llm.connection_factory import AdapterRuntimeOptions, EmbeddingProtocolAdapter
from openbiliclaw.llm.dashscope_provider import DashScopeEmbeddingProvider
from openbiliclaw.llm.embedding import EmbeddingService
from openbiliclaw.llm.registry import (
    RegistryBuildError,
    build_ordered_embedding_service,
)
from openbiliclaw.model_config import (
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
)


def test_is_multimodal_embedding_model_markers() -> None:
    assert DashScopeEmbeddingProvider.is_multimodal_embedding_model("qwen3-vl-embedding")
    assert DashScopeEmbeddingProvider.is_multimodal_embedding_model("tongyi-embedding-vision-plus")
    assert DashScopeEmbeddingProvider.is_multimodal_embedding_model("multimodal-embedding-v1")
    assert not DashScopeEmbeddingProvider.is_multimodal_embedding_model("text-embedding-v3")
    assert not DashScopeEmbeddingProvider.is_multimodal_embedding_model("")


def test_protocol_adapter_image_capability_respects_the_shared_model() -> None:
    provider = DashScopeEmbeddingProvider(api_key="sk-test")
    text_only = EmbeddingProtocolAdapter(
        name="dashscope-text",
        connection_type="dashscope_api",
        preset="",
        settings=EmbeddingModelSettings(
            model="text-embedding-v3",
            multimodal_enabled=True,
        ),
        provider=provider,
    )
    multimodal = EmbeddingProtocolAdapter(
        name="dashscope-image",
        connection_type="dashscope_api",
        preset="",
        settings=EmbeddingModelSettings(
            model="qwen3-vl-embedding",
            multimodal_enabled=True,
        ),
        provider=provider,
    )

    assert text_only.supports_image_embedding is False
    assert multimodal.supports_image_embedding is True


@pytest.mark.asyncio
async def test_complete_is_embedding_only() -> None:
    provider = DashScopeEmbeddingProvider(api_key="sk-test")
    with pytest.raises(Exception, match="embedding-only"):
        await provider.complete([{"role": "user", "content": "hi"}])


def _mock_response(payload: dict[str, Any], *, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = str(payload)
    response.json.return_value = payload
    return response


@pytest.mark.asyncio
async def test_embed_text_parses_vector() -> None:
    provider = DashScopeEmbeddingProvider(
        api_key="sk-test",
        model="qwen3-vl-embedding",
        embedding_output_dimensionality=1024,
    )
    payload = {
        "output": {
            "embeddings": [
                {
                    "index": 0,
                    "type": "text",
                    "embedding": [0.1, 0.2, 0.3],
                }
            ]
        },
        "request_id": "req-1",
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=_mock_response(payload))

    with patch(
        "openbiliclaw.llm.dashscope_provider.httpx.AsyncClient",
        return_value=mock_client,
    ):
        vector = await provider.embed("游戏攻略封面风格")

    assert vector == [0.1, 0.2, 0.3]
    call_kwargs = mock_client.post.await_args
    assert call_kwargs is not None
    url = call_kwargs.args[0]
    assert url.endswith("/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding")
    body = call_kwargs.kwargs["json"]
    assert body["model"] == "qwen3-vl-embedding"
    assert body["input"]["contents"] == [{"text": "游戏攻略封面风格"}]
    assert body["parameters"]["dimension"] == 1024
    assert "enable_fusion" not in body.get("parameters", {})


@pytest.mark.asyncio
async def test_embed_image_sends_data_uri() -> None:
    provider = DashScopeEmbeddingProvider(api_key="sk-test", model="qwen3-vl-embedding")
    payload = {
        "output": {
            "embeddings": [
                {"index": 0, "type": "image", "embedding": [0.5, 0.5]},
            ]
        }
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=_mock_response(payload))

    with patch(
        "openbiliclaw.llm.dashscope_provider.httpx.AsyncClient",
        return_value=mock_client,
    ):
        vector = await provider.embed_image(b"\xff\xd8\xffjpeg", mime_type="image/jpeg")

    assert vector == [0.5, 0.5]
    body = mock_client.post.await_args.kwargs["json"]
    image_field = body["input"]["contents"][0]["image"]
    assert image_field.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_embed_image_rejects_qwen25_independent() -> None:
    provider = DashScopeEmbeddingProvider(api_key="sk-test", model="qwen2.5-vl-embedding")
    vector = await provider.embed_image(
        b"bytes",
        mime_type="image/jpeg",
        model="qwen2.5-vl-embedding",
    )
    assert vector == []


@pytest.mark.asyncio
async def test_embed_raises_typed_secret_safe_error_on_api_error_payload() -> None:
    provider = DashScopeEmbeddingProvider(api_key="sk-test")
    sentinel = "dashscope-payload-secret-never-retain"
    payload = {"code": "InvalidApiKey", "message": sentinel}
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=_mock_response(payload))

    with (
        patch(
            "openbiliclaw.llm.dashscope_provider.httpx.AsyncClient",
            return_value=mock_client,
        ),
        pytest.raises(LLMProviderError) as raised,
    ):
        await provider.embed("private text")

    assert "authentication failed" in str(raised.value)
    assert sentinel not in str(raised.value)
    assert "private text" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_embed_raises_typed_secret_safe_error_on_malformed_json() -> None:
    provider = DashScopeEmbeddingProvider(api_key="sk-test")
    sentinel = "dashscope-json-secret-never-retain"
    response = _mock_response({})
    response.json.side_effect = ValueError(sentinel)
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=response)

    with (
        patch(
            "openbiliclaw.llm.dashscope_provider.httpx.AsyncClient",
            return_value=mock_client,
        ),
        pytest.raises(LLMResponseError) as raised,
    ):
        await provider.embed("private text")

    assert sentinel not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_embed_retries_http_server_failure_then_raises_secret_safe_error() -> None:
    provider = DashScopeEmbeddingProvider(api_key="sk-test")
    sentinel = "dashscope-response-secret-never-retain"
    response = _mock_response({"error": sentinel}, status_code=503)
    response.text = sentinel
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=response)

    with (
        patch(
            "openbiliclaw.llm.dashscope_provider.httpx.AsyncClient",
            return_value=mock_client,
        ),
        patch("asyncio.sleep", new=AsyncMock()),
        pytest.raises(LLMProviderError) as raised,
    ):
        await provider.embed("private text")

    assert mock_client.post.await_count == 3
    assert sentinel not in str(raised.value)
    assert "private text" not in str(raised.value)


@pytest.mark.asyncio
async def test_embedding_service_dashscope_image_active() -> None:
    provider = DashScopeEmbeddingProvider(api_key="sk-test", model="qwen3-vl-embedding")
    service = EmbeddingService(
        provider,
        model="qwen3-vl-embedding",
        multimodal_enabled=True,
    )
    assert service.supports_image_embedding is True
    assert service.image_embedding_active() is True

    payload = {
        "output": {
            "embeddings": [{"index": 0, "type": "image", "embedding": [1.0, 0.0]}],
        }
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=_mock_response(payload))

    with patch(
        "openbiliclaw.llm.dashscope_provider.httpx.AsyncClient",
        return_value=mock_client,
    ):
        vec = await service.embed_image(b"cover", mime_type="image/jpeg")
    assert vec == [1.0, 0.0]


def test_build_ordered_embedding_service_dashscope() -> None:
    route = EmbeddingRouteConfig(
        enabled=True,
        settings=EmbeddingModelSettings(
            model="qwen3-vl-embedding",
            output_dimensionality=1024,
            multimodal_enabled=True,
        ),
        providers=(
            EmbeddingProviderConfig(
                id="dashscope-main",
                name="DashScope",
                type="dashscope_api",
                credential=CredentialConfig(source="inline", value="sk-dashscope-test"),
            ),
        ),
    )

    service = build_ordered_embedding_service(
        route,
        revision="rev-1",
        runtime_options=AdapterRuntimeOptions(environment={}),
    )
    assert service is not None
    assert service.image_embedding_active() is True
    ordered_route = service._provider  # type: ignore[attr-defined]
    assert isinstance(ordered_route.providers[0].provider, DashScopeEmbeddingProvider)


def test_build_ordered_embedding_service_dashscope_missing_key() -> None:
    route = EmbeddingRouteConfig(
        enabled=True,
        settings=EmbeddingModelSettings(model="qwen3-vl-embedding"),
        providers=(
            EmbeddingProviderConfig(
                id="dashscope-main",
                name="DashScope",
                type="dashscope_api",
                credential=CredentialConfig(source="env", value="DASHSCOPE_API_KEY"),
            ),
        ),
    )

    with pytest.raises(RegistryBuildError, match="credential"):
        build_ordered_embedding_service(
            route,
            revision="rev-1",
            runtime_options=AdapterRuntimeOptions(environment={}),
        )


def test_dashscope_honors_output_dimensionality() -> None:
    provider = DashScopeEmbeddingProvider(
        api_key="sk-test",
        embedding_output_dimensionality=1024,
    )
    assert provider._dimension_for_model("qwen3-vl-embedding") == 1024
    assert provider._dimension_for_model("tongyi-embedding-vision-plus") is None


def test_base_url_strips_compatible_mode_suffix() -> None:
    provider = DashScopeEmbeddingProvider(
        api_key="sk-test",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    assert provider._base_url == "https://dashscope.aliyuncs.com"


@pytest.mark.asyncio
async def test_embed_routes_per_endpoint_domestic_direct() -> None:
    """Pitfall rule 1 + v0.3.167: the DashScope client routes per endpoint via
    network.httpx_kwargs_for_endpoint. dashscope.aliyuncs.com is domestic, so it
    stays direct (trust_env=False, no proxy) even when [network].mode is custom
    for reaching overseas models — the CN embedding call never tunnels the ladder.
    A genuinely non-domestic base_url still follows the global mode.
    """
    from openbiliclaw import network

    payload = {"output": {"embeddings": [{"index": 0, "type": "text", "embedding": [0.1]}]}}

    def _make_factory() -> MagicMock:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=_mock_response(payload))
        return MagicMock(return_value=mock_client)

    async def _capture(provider: DashScopeEmbeddingProvider) -> MagicMock:
        factory = _make_factory()
        with patch("openbiliclaw.llm.dashscope_provider.httpx.AsyncClient", factory):
            await provider.embed("hi")
        return factory

    domestic = DashScopeEmbeddingProvider(api_key="sk-test", model="qwen3-vl-embedding")
    offshore = DashScopeEmbeddingProvider(
        api_key="sk-test", model="qwen3-vl-embedding", base_url="https://relay.example.com"
    )

    network.reset_outbound_proxy_for_tests()
    try:
        # Domestic endpoint, direct mode → direct, no proxy.
        f = await _capture(domestic)
        assert f.call_args.kwargs.get("trust_env") is False
        assert "proxy" not in f.call_args.kwargs

        # Domestic endpoint, CUSTOM mode → STILL direct (domestic carve-out).
        network.set_outbound_proxy("http://127.0.0.1:7890")
        f = await _capture(domestic)
        assert f.call_args.kwargs.get("trust_env") is False
        assert "proxy" not in f.call_args.kwargs

        # Non-domestic base_url, custom mode → follows global mode (proxy applied).
        f = await _capture(offshore)
        assert f.call_args.kwargs.get("proxy") == "http://127.0.0.1:7890"
        assert f.call_args.kwargs.get("trust_env") is False
    finally:
        network.reset_outbound_proxy_for_tests()
