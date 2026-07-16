"""Native ordered-route factory contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from openbiliclaw.llm import connection_factory
from openbiliclaw.llm import registry as registry_module
from openbiliclaw.llm.connection_factory import AdapterRuntimeOptions
from openbiliclaw.llm.openai_provider import OpenAIProtocolProvider
from openbiliclaw.llm.registry import (
    RegistryBuildError,
    build_ordered_chat_route,
    build_ordered_embedding_service,
)
from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
)


def test_registry_module_has_only_native_ordered_route_construction() -> None:
    root = Path(__file__).resolve().parents[1]
    runtime_source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "src/openbiliclaw/llm/base.py",
            "src/openbiliclaw/llm/registry.py",
            "src/openbiliclaw/llm/openai_provider.py",
        )
    )
    assert not hasattr(registry_module, "build_llm_registry")
    assert not hasattr(registry_module, "build_embedding_service")
    assert not hasattr(registry_module, "summarize_registry")
    assert "class LLMRegistry" not in runtime_source
    assert "class DeepSeekProvider" not in runtime_source
    assert "class OpenRouterProvider" not in runtime_source


@dataclass
class _ChatAdapter:
    name: str


@dataclass(frozen=True)
class _EmbeddingAdapter:
    name: str
    connection_type: str
    preset: str
    settings: EmbeddingModelSettings
    supports_image_embedding: bool = False

    async def embed(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0]

    async def embed_image(
        self, image_bytes: bytes, *, mime_type: str = "image/jpeg"
    ) -> list[float]:
        del image_bytes, mime_type
        return []


def _chat(connection_id: str, *, preset: str = "custom") -> ChatConnection:
    return ChatConnection(
        id=connection_id,
        name=connection_id,
        type="openai_compatible",
        preset=preset,
        model=f"{connection_id}-model",
        base_url="https://example.com/v1",
        credential=CredentialConfig(source="inline", value="secret"),
        api_mode="chat_completions",
    )


def test_chat_factory_preserves_configured_array_order(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = ChatRouteConfig(connections=(_chat("first"), _chat("second")))
    monkeypatch.setattr(
        connection_factory,
        "build_chat_adapter",
        lambda connection, _options: _ChatAdapter(connection.id),
    )

    route = build_ordered_chat_route(
        configured,
        revision="rev-1",
        runtime_options=AdapterRuntimeOptions(environment={}),
    )

    assert [item.id for item in route.connections] == ["first", "second"]
    assert [item.adapter.name for item in route.connections] == ["first", "second"]
    assert route.revision == "rev-1"


def test_chat_factory_rejects_an_empty_route() -> None:
    with pytest.raises(RegistryBuildError, match="no connections"):
        build_ordered_chat_route(
            ChatRouteConfig(),
            revision="rev-1",
            runtime_options=AdapterRuntimeOptions(environment={}),
        )


def test_deepseek_and_openrouter_use_one_openai_protocol_adapter() -> None:
    deepseek = replace(_chat("deepseek", preset="deepseek"), base_url="")
    openrouter = ChatConnection(
        **{
            **_chat("openrouter", preset="openrouter").__dict__,
            "http_referer": "https://example.com",
            "x_title": "OpenBiliClaw",
        }
    )

    route = build_ordered_chat_route(
        ChatRouteConfig(connections=(deepseek, openrouter)),
        revision="rev-unified",
        runtime_options=AdapterRuntimeOptions(environment={}),
    )

    assert all(isinstance(item.adapter, OpenAIProtocolProvider) for item in route.connections)
    assert route.connections[0].adapter.options.preset == "deepseek"
    assert route.connections[0].adapter.base_url == "https://api.deepseek.com"
    assert dict(route.connections[1].adapter.options.extra_headers) == {
        "HTTP-Referer": "https://example.com",
        "X-Title": "OpenBiliClaw",
    }


def test_chat_factory_wraps_adapter_construction_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("private-constructor-detail")

    monkeypatch.setattr(connection_factory, "build_chat_adapter", fail)
    with pytest.raises(RegistryBuildError, match="Chat route construction failed"):
        build_ordered_chat_route(
            ChatRouteConfig(connections=(_chat("one"),)),
            revision="rev-1",
            runtime_options=AdapterRuntimeOptions(environment={}),
        )


def test_disabled_embedding_route_returns_none() -> None:
    assert (
        build_ordered_embedding_service(
            EmbeddingRouteConfig(enabled=False),
            revision="rev-1",
            runtime_options=AdapterRuntimeOptions(environment={}),
        )
        is None
    )


def test_enabled_embedding_route_requires_providers() -> None:
    with pytest.raises(RegistryBuildError, match="no providers"):
        build_ordered_embedding_service(
            EmbeddingRouteConfig(
                enabled=True,
                settings=EmbeddingModelSettings(model="bge-m3"),
            ),
            revision="rev-1",
            runtime_options=AdapterRuntimeOptions(environment={}),
        )


def test_embedding_factory_shares_one_settings_identity_and_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = EmbeddingModelSettings(model="shared", output_dimensionality=2)
    providers = (
        EmbeddingProviderConfig(id="one", name="one", type="ollama"),
        EmbeddingProviderConfig(id="two", name="two", type="ollama"),
    )
    monkeypatch.setattr(
        connection_factory,
        "build_embedding_adapter",
        lambda provider, route_settings, _options: _EmbeddingAdapter(
            name=provider.id,
            connection_type=provider.type,
            preset=provider.preset,
            settings=route_settings,
        ),
    )

    service = build_ordered_embedding_service(
        EmbeddingRouteConfig(enabled=True, settings=settings, providers=providers),
        revision="rev-embedding",
        runtime_options=AdapterRuntimeOptions(environment={}),
    )

    assert service is not None
    assert service._shared_settings is settings
    assert [item.name for item in service._provider.providers] == ["one", "two"]
    assert all(item.settings is settings for item in service._provider.providers)
