"""Build runtime protocol adapters from immutable model connections."""

from __future__ import annotations

import ipaddress
import os
import unicodedata
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, cast
from urllib.parse import urlsplit

from openbiliclaw import network
from openbiliclaw.model_config.registry import connection_type_registry

from .anthropic_provider import AnthropicCompatibleProvider
from .base import LLMProvider, LLMProviderError
from .codex_auth import load_codex_access_token, validated_codex_api_base_url
from .dashscope_provider import DashScopeEmbeddingProvider
from .gemini_provider import GeminiProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProtocolOptions, OpenAIProtocolProvider

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from openbiliclaw.model_config import (
        ChatConnection,
        CredentialConfig,
        EmbeddingModelSettings,
        EmbeddingProviderConfig,
    )

_OPENAI_PRESET_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api/v1",
}
_ANTHROPIC_OFFICIAL_ENDPOINT = "https://api.anthropic.com"
_GEMINI_OFFICIAL_ENDPOINT = "https://generativelanguage.googleapis.com"
_OLLAMA_DEFAULT_ENDPOINT = "http://127.0.0.1:11434/v1"


@dataclass(frozen=True)
class AdapterRuntimeOptions:
    """Secret-safe construction inputs shared by connection factories."""

    timeout_seconds: float = 300.0
    environment: Mapping[str, str] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    codex_token_loader: Callable[[], str] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        """Freeze the exact caller-supplied environment snapshot."""
        if self.environment is None:
            return
        try:
            snapshot = dict(self.environment)
        except Exception:
            snapshot = None
        if snapshot is None:
            raise LLMProviderError("connection runtime options are invalid") from None
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in snapshot.items()
        ):
            raise LLMProviderError("connection runtime options are invalid")
        object.__setattr__(self, "environment", MappingProxyType(snapshot))


class _EmbeddingProvider(Protocol):
    async def embed(self, text: str, *, model: str = ...) -> list[float]: ...


class SupportsEmbedding(Protocol):
    """Runtime embedding adapter bound to one shared model-space object."""

    @property
    def name(self) -> str: ...

    @property
    def connection_type(self) -> str: ...

    @property
    def preset(self) -> str: ...

    @property
    def settings(self) -> EmbeddingModelSettings: ...

    @property
    def provider(self) -> _EmbeddingProvider: ...

    @property
    def supports_image_embedding(self) -> bool: ...

    async def embed(self, text: str) -> list[float]: ...

    async def embed_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
    ) -> list[float]: ...


@dataclass(frozen=True)
class EmbeddingProtocolAdapter:
    """Bind a native provider to one immutable shared embedding model space."""

    name: str
    connection_type: str
    preset: str
    settings: EmbeddingModelSettings
    provider: _EmbeddingProvider = field(repr=False)

    @property
    def supports_image_embedding(self) -> bool:
        if not bool(getattr(self.provider, "supports_image_embedding", False)):
            return False
        checker = getattr(self.provider, "is_multimodal_embedding_model", None)
        if callable(checker) and not bool(checker(self.settings.model)):
            return False
        return callable(getattr(self.provider, "embed_image", None))

    async def embed(self, text: str) -> list[float]:
        return await self.provider.embed(text, model=self.settings.model)

    async def embed_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
    ) -> list[float]:
        method = getattr(self.provider, "embed_image", None)
        if method is None:
            return []
        result = await method(
            image_bytes,
            mime_type=mime_type,
            model=self.settings.model,
        )
        return cast("list[float]", result)


def build_chat_adapter(
    connection: ChatConnection,
    runtime_options: AdapterRuntimeOptions,
) -> LLMProvider:
    """Construct one named chat adapter without retaining credential sources."""
    _require_capability(connection.type, connection.preset, "chat")
    timeout = _timeout(runtime_options)

    if connection.type == "codex_oauth":
        endpoint = _validate_codex_before_token_lookup(connection)
        api_key = _resolve_credential(
            connection.credential,
            connection_type=connection.type,
            capability="chat",
            runtime_options=runtime_options,
            oauth_allowed=True,
        )
        proxy, trust_env = _transport_for(endpoint)
        return OpenAIProtocolProvider(
            api_key=api_key,
            model=connection.model,
            base_url=endpoint,
            options=OpenAIProtocolOptions(
                connection_id=connection.id,
                preset="openai",
                api_mode="chat_completions",
            ),
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
        )

    api_key = _resolve_credential(
        connection.credential,
        connection_type=connection.type,
        capability="chat",
        runtime_options=runtime_options,
    )
    if connection.type == "openai_compatible":
        endpoint = _openai_endpoint(connection.preset, connection.base_url)
        proxy, trust_env = _transport_for(endpoint)
        return OpenAIProtocolProvider(
            api_key=api_key,
            model=connection.model,
            base_url=endpoint,
            options=OpenAIProtocolOptions(
                connection_id=connection.id,
                preset=connection.preset,
                api_mode=_api_mode(connection.api_mode),
                default_reasoning_effort=connection.reasoning_effort,
                extra_headers=_openrouter_headers(connection),
            ),
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
        )
    if connection.type == "anthropic_compatible":
        endpoint = _anthropic_endpoint(connection.preset, connection.base_url)
        proxy, trust_env = _transport_for(endpoint)
        return AnthropicCompatibleProvider(
            connection_id=connection.id,
            api_key=api_key,
            model=connection.model,
            base_url=endpoint,
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
        )
    if connection.type == "gemini_api":
        endpoint = connection.base_url.strip()
        proxy, trust_env = _transport_for(endpoint or _GEMINI_OFFICIAL_ENDPOINT)
        return GeminiProvider(
            api_key=api_key,
            model=connection.model,
            timeout=timeout,
            base_url=endpoint,
            proxy=proxy,
            trust_env=trust_env,
            provider_name=connection.id,
        )
    if connection.type == "ollama":
        endpoint = _ollama_endpoint(connection.base_url)
        return OllamaProvider(
            api_key=api_key,
            model=connection.model,
            base_url=endpoint,
            timeout=timeout,
            num_ctx=connection.num_ctx,
            provider_name=connection.id,
            trust_env=False,
        )
    raise LLMProviderError("connection type is not supported")


def build_embedding_adapter(
    provider: EmbeddingProviderConfig,
    settings: EmbeddingModelSettings,
    runtime_options: AdapterRuntimeOptions,
) -> SupportsEmbedding:
    """Construct one embedding adapter bound to the exact shared settings."""
    _require_capability(provider.type, provider.preset, "embedding")
    timeout = _timeout(runtime_options)
    api_key = _resolve_credential(
        provider.credential,
        connection_type=provider.type,
        capability="embedding",
        runtime_options=runtime_options,
    )
    native: _EmbeddingProvider

    if provider.type == "openai_compatible":
        endpoint = _openai_endpoint(provider.preset, provider.base_url)
        proxy, trust_env = _transport_for(endpoint)
        native = OpenAIProtocolProvider(
            api_key=api_key,
            model=settings.model,
            base_url=endpoint,
            options=OpenAIProtocolOptions(
                connection_id=provider.id,
                preset=provider.preset,
                api_mode="chat_completions",
            ),
            timeout=timeout,
            embedding_output_dimensionality=settings.output_dimensionality,
            proxy=proxy,
            trust_env=trust_env,
        )
    elif provider.type == "gemini_api":
        endpoint = provider.base_url.strip()
        proxy, trust_env = _transport_for(endpoint or _GEMINI_OFFICIAL_ENDPOINT)
        native = GeminiProvider(
            api_key=api_key,
            model=settings.model,
            timeout=timeout,
            base_url=endpoint,
            embedding_output_dimensionality=settings.output_dimensionality,
            proxy=proxy,
            trust_env=trust_env,
            provider_name=provider.id,
        )
    elif provider.type == "dashscope_api":
        native = DashScopeEmbeddingProvider(
            api_key=api_key,
            model=settings.model,
            base_url=provider.base_url,
            timeout=timeout,
            embedding_output_dimensionality=settings.output_dimensionality,
        )
    elif provider.type == "ollama":
        native = OllamaProvider(
            api_key=api_key,
            model=settings.model,
            base_url=_ollama_endpoint(provider.base_url),
            timeout=timeout,
            provider_name=provider.id,
            trust_env=False,
        )
    else:  # pragma: no cover - guarded by registry capability lookup
        raise LLMProviderError("connection type is not supported")
    return EmbeddingProtocolAdapter(
        name=provider.id,
        connection_type=provider.type,
        preset=provider.preset,
        settings=settings,
        provider=native,
    )


def _require_capability(connection_type: str, preset: str, capability: str) -> None:
    definition = connection_type_registry().get(connection_type)
    if definition is None:
        raise LLMProviderError("connection type is not supported")
    if capability not in definition.capabilities:
        raise LLMProviderError("connection capability is not supported")
    if not definition.presets:
        return
    selected = next((item for item in definition.presets if item.id == preset), None)
    if selected is None or capability not in selected.capabilities:
        raise LLMProviderError("connection capability is not supported")


def _resolve_credential(
    credential: CredentialConfig,
    *,
    connection_type: str,
    capability: str,
    runtime_options: AdapterRuntimeOptions,
    oauth_allowed: bool = False,
) -> str:
    if credential.source == "inline":
        value = credential.value
    elif credential.source == "env":
        environment = (
            os.environ if runtime_options.environment is None else runtime_options.environment
        )
        value = environment.get(credential.value, "")
    elif credential.source == "oauth":
        if not oauth_allowed or credential.value != "codex":
            raise LLMProviderError("connection credential is unavailable")
        loader = runtime_options.codex_token_loader or load_codex_access_token
        try:
            value = loader()
        except Exception:
            value = ""
    elif credential.source == "none" and _descriptor_allows_no_credential(
        connection_type,
        capability,
    ):
        return "ollama"
    else:
        raise LLMProviderError("connection credential is unavailable")
    if not isinstance(value, str) or not value.strip():
        raise LLMProviderError("connection credential is unavailable")
    return value


def _descriptor_allows_no_credential(connection_type: str, capability: str) -> bool:
    definition = connection_type_registry().get(connection_type)
    if definition is None or capability not in definition.capabilities:
        return False
    return all(field.name != "credential" for field in definition.fields)


def _validate_codex_before_token_lookup(connection: ChatConnection) -> str:
    if connection.credential.source != "oauth" or connection.credential.value != "codex":
        raise LLMProviderError("connection credential is unavailable")
    try:
        endpoint = validated_codex_api_base_url(connection.base_url)
    except Exception:
        endpoint = None
    if endpoint is None:
        raise LLMProviderError("connection endpoint is not allowed") from None
    return endpoint


def _timeout(runtime_options: AdapterRuntimeOptions) -> float:
    timeout = float(runtime_options.timeout_seconds)
    if timeout <= 0:
        raise LLMProviderError("connection runtime options are invalid")
    return timeout


def _transport_for(endpoint: str) -> tuple[str, bool]:
    proxy = network.proxy_for_endpoint(endpoint) or ""
    trust_env = network.trust_env_for_endpoint(endpoint)
    return proxy, trust_env


def _api_mode(value: str) -> Literal["chat_completions", "responses"]:
    normalized = value.strip().lower() or "chat_completions"
    if normalized not in {"chat_completions", "responses"}:
        raise LLMProviderError("connection configuration is invalid")
    return cast('Literal["chat_completions", "responses"]', normalized)


def _openai_endpoint(preset: str, base_url: str) -> str:
    normalized_preset = preset.strip().lower()
    if normalized_preset == "custom":
        return _validated_custom_endpoint(base_url)
    endpoint = base_url.strip() or _OPENAI_PRESET_ENDPOINTS.get(normalized_preset, "")
    if not endpoint:
        raise LLMProviderError("connection configuration is invalid")
    return endpoint


def _anthropic_endpoint(preset: str, base_url: str) -> str:
    endpoint = base_url.strip()
    if preset == "anthropic":
        return endpoint or _ANTHROPIC_OFFICIAL_ENDPOINT
    if preset == "custom":
        return _validated_custom_endpoint(base_url)
    raise LLMProviderError("connection configuration is invalid")


def _validated_custom_endpoint(value: str) -> str:
    """Return one safe HTTP(S) endpoint without changing its path or port."""
    candidate = value
    invalid_text = (
        not candidate
        or candidate != candidate.strip()
        or "\\" in candidate
        or "?" in candidate
        or "#" in candidate
        or any(unicodedata.category(character) == "Cc" for character in candidate)
    )
    if invalid_text:
        raise LLMProviderError("connection endpoint is invalid")
    parsed = None
    hostname = None
    port = None
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        parsed = None
        hostname = None
        port = None
    if parsed is None:
        raise LLMProviderError("connection endpoint is invalid") from None
    invalid_structure = (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or (port is not None and port <= 0)
        or not _valid_endpoint_hostname(hostname)
    )
    if invalid_structure:
        raise LLMProviderError("connection endpoint is invalid")
    return candidate


def _valid_endpoint_hostname(hostname: str) -> bool:
    if hostname.endswith(".."):
        return False
    normalized = hostname[:-1] if hostname.endswith(".") else hostname
    if not normalized:
        return False
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        try:
            ascii_name = normalized.encode("idna").decode("ascii")
        except UnicodeError:
            return False
        if len(ascii_name) > 253:
            return False
        labels = ascii_name.split(".")
        return all(
            0 < len(label) <= 63
            and not label.startswith("-")
            and not label.endswith("-")
            and all(
                character.isascii() and (character.isalnum() or character == "-")
                for character in label
            )
            for label in labels
        )
    return True


def _ollama_endpoint(base_url: str) -> str:
    endpoint = base_url.strip() or _OLLAMA_DEFAULT_ENDPOINT
    if not endpoint.rstrip("/").endswith("/v1"):
        endpoint = endpoint.rstrip("/") + "/v1"
    return endpoint


def _openrouter_headers(connection: ChatConnection) -> dict[str, str]:
    if connection.preset != "openrouter":
        return {}
    headers: dict[str, str] = {}
    if connection.http_referer.strip():
        headers["HTTP-Referer"] = connection.http_referer
    if connection.x_title.strip():
        headers["X-Title"] = connection.x_title
    return headers
