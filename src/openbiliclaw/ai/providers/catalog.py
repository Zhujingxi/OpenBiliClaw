"""Typed models.dev catalog loading, caching, and model resolution."""

from __future__ import annotations

import time
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import ConfigDict, Field, TypeAdapter

from openbiliclaw.ai.runtime.capabilities import ModelCapabilities
from openbiliclaw.core._pydantic import StrictBaseModel

CATALOG_URL = "https://models.dev/api.json"
CATALOG_TTL_SECONDS = 24 * 60 * 60


class CatalogError(RuntimeError):
    """The model catalog could not be loaded or resolved."""


class UnsupportedProtocolError(CatalogError):
    """A models.dev SDK marker has no supported PydanticAI protocol path."""


class CatalogLimit(StrictBaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    context: int = Field(ge=0)
    output: int = Field(ge=0)


class CatalogModel(StrictBaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    id: str = Field(min_length=1)
    name: str | None = None
    reasoning: bool = False
    tool_call: bool = False
    structured_output: bool = False
    attachment: bool = False
    limit: CatalogLimit

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            tools=self.tool_call,
            structured_output=self.structured_output,
            vision=self.attachment,
            context_tokens=self.limit.context,
            streaming=True,
            reasoning=self.reasoning,
        )


class CatalogProvider(StrictBaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)

    id: str = Field(min_length=1)
    name: str | None = None
    npm: str = Field(min_length=1)
    api: str | None = None
    env: tuple[str, ...] = ()
    doc: str | None = None
    models: dict[str, CatalogModel]


_CATALOG_ADAPTER = TypeAdapter(dict[str, CatalogProvider])


class ProviderCatalog:
    """Typed wrapper around models.dev's provider-keyed root object."""

    def __init__(self, root: dict[str, CatalogProvider]) -> None:
        self.root = root

    @classmethod
    def model_validate_json(cls, payload: bytes) -> ProviderCatalog:
        return cls(_CATALOG_ADAPTER.validate_json(payload))


Protocol: TypeAlias = Literal["openai", "anthropic", "google", "openrouter"]

_PROTOCOLS: dict[str, Protocol] = {
    "@ai-sdk/openai": "openai",
    "@ai-sdk/openai-compatible": "openai",
    "@ai-sdk/anthropic": "anthropic",
    "@ai-sdk/google": "google",
    "@openrouter/ai-sdk-provider": "openrouter",
}


class CapabilityConfig(StrictBaseModel):
    """Complete user override of catalog capability metadata."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    tools: bool
    structured_output: bool
    vision: bool
    context_tokens: int = Field(ge=0)
    streaming: bool
    reasoning: bool

    def runtime(self) -> ModelCapabilities:
        return ModelCapabilities(
            tools=self.tools,
            structured_output=self.structured_output,
            vision=self.vision,
            context_tokens=self.context_tokens,
            streaming=self.streaming,
            reasoning=self.reasoning,
        )


class ResolvedModel(StrictBaseModel):
    """Catalog-normalized non-secret model configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    provider: str
    protocol: Protocol
    endpoint: str | None
    capabilities: ModelCapabilities


Fetch = Callable[[str], bytes]


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "OpenBiliClaw/1"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload: bytes = response.read()
        return payload


class ModelCatalog:
    """Load models.dev live with a 24-hour cache and offline fallback."""

    def __init__(self, cache_path: Path, *, fetch: Fetch = _fetch) -> None:
        self._cache_path = cache_path
        self._fetch = fetch

    def load(self, *, now: float | None = None) -> ProviderCatalog:
        current = time.time() if now is None else now
        cached = self._read_cache()
        if cached is not None and current - self._cache_path.stat().st_mtime < CATALOG_TTL_SECONDS:
            return cached
        try:
            payload = self._fetch(CATALOG_URL)
            catalog = ProviderCatalog.model_validate_json(payload)
        except Exception as error:
            if cached is not None:
                return cached
            raise CatalogError(
                f"models.dev catalog is unavailable and no cache exists at {self._cache_path}"
            ) from error
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._cache_path.with_suffix(self._cache_path.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(self._cache_path)
        return catalog

    def _read_cache(self) -> ProviderCatalog | None:
        try:
            return ProviderCatalog.model_validate_json(self._cache_path.read_bytes())
        except (FileNotFoundError, ValueError):
            return None


def protocol_for(npm: str) -> Protocol:
    try:
        return _PROTOCOLS[npm]
    except KeyError as error:
        raise UnsupportedProtocolError(f"unsupported models.dev protocol marker: {npm}") from error


def resolve_model(
    catalog: ProviderCatalog,
    *,
    provider_id: str,
    model_name: str,
    endpoint: str | None,
    protocol: Protocol | None,
    capabilities: CapabilityConfig | None,
) -> ResolvedModel:
    """Resolve a catalog model or validate a fully explicit custom provider."""

    provider = catalog.root.get(provider_id)
    if provider is None:
        if protocol is None or endpoint is None or capabilities is None:
            raise CatalogError(
                f"custom provider {provider_id!r} requires protocol, endpoint, and capabilities"
            )
        return ResolvedModel(
            provider=provider_id,
            protocol=protocol,
            endpoint=endpoint,
            capabilities=capabilities.runtime(),
        )
    model = provider.models.get(model_name)
    if model is None:
        raise CatalogError(f"model {model_name!r} is not listed for provider {provider_id!r}")
    return ResolvedModel(
        provider=provider.id,
        protocol=protocol_for(provider.npm),
        endpoint=endpoint if endpoint is not None else provider.api,
        capabilities=capabilities.runtime() if capabilities is not None else model.capabilities(),
    )
