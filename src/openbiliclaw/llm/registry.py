"""Native factories for ordered model routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import connection_factory as _connection_factory
from .base import LLMProvider, LLMProviderError

if TYPE_CHECKING:
    from openbiliclaw.llm.embedding import EmbeddingCache, SupportsEmbeddingService
    from openbiliclaw.llm.route import CircuitTable, OrderedLLMRoute
    from openbiliclaw.model_config import (
        ChatConnection,
        ChatRouteConfig,
        EmbeddingModelSettings,
        EmbeddingProviderConfig,
        EmbeddingRouteConfig,
    )

    from .connection_factory import AdapterRuntimeOptions, SupportsEmbedding


class RegistryBuildError(LLMProviderError):
    """Raised when a configured ordered route cannot be constructed."""


def build_chat_adapter(
    connection: ChatConnection,
    runtime_options: AdapterRuntimeOptions,
) -> LLMProvider:
    """Patchable forwarding seam to the unified connection factory."""
    return _connection_factory.build_chat_adapter(connection, runtime_options)


def build_embedding_adapter(
    provider: EmbeddingProviderConfig,
    settings: EmbeddingModelSettings,
    runtime_options: AdapterRuntimeOptions,
) -> SupportsEmbedding:
    """Patchable forwarding seam to the unified connection factory."""
    return _connection_factory.build_embedding_adapter(
        provider,
        settings,
        runtime_options,
    )


def build_ordered_chat_route(
    route_config: ChatRouteConfig,
    *,
    revision: str,
    runtime_options: AdapterRuntimeOptions,
    circuits: CircuitTable | None = None,
) -> OrderedLLMRoute:
    """Bind every configured Chat connection in exact array order."""
    if not route_config.connections:
        raise RegistryBuildError("Chat route has no connections.")

    from openbiliclaw.llm.route import OrderedLLMRoute, RouteConnection

    try:
        connections = tuple(
            RouteConnection(
                connection=connection,
                adapter=build_chat_adapter(connection, runtime_options),
            )
            for connection in route_config.connections
        )
    except RegistryBuildError:
        raise
    except Exception as exc:
        raise RegistryBuildError(f"Chat route construction failed: {exc}") from exc
    return OrderedLLMRoute(
        connections,
        revision=revision,
        timeout_seconds=float(route_config.timeout_seconds),
        circuits=circuits,
    )


def build_ordered_embedding_service(
    route_config: EmbeddingRouteConfig,
    *,
    revision: str,
    runtime_options: AdapterRuntimeOptions,
    persistent_cache: EmbeddingCache | None = None,
    circuits: CircuitTable | None = None,
) -> SupportsEmbeddingService | None:
    """Bind an enabled embedding route to its shared model-space settings."""
    if not route_config.enabled:
        return None
    if not route_config.providers:
        raise RegistryBuildError("Enabled embedding route has no providers.")

    from openbiliclaw.llm.embedding import EmbeddingService
    from openbiliclaw.llm.embedding_route import OrderedEmbeddingRoute

    try:
        adapters = tuple(
            build_embedding_adapter(
                provider,
                route_config.settings,
                runtime_options,
            )
            for provider in route_config.providers
        )
    except RegistryBuildError:
        raise
    except Exception as exc:
        raise RegistryBuildError(f"Embedding route construction failed: {exc}") from exc
    route = OrderedEmbeddingRoute(
        adapters,
        settings=route_config.settings,
        revision=revision,
        circuits=circuits,
    )
    return EmbeddingService(route, persistent_cache=persistent_cache)
