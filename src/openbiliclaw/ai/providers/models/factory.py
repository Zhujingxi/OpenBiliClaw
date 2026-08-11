"""Explicit trusted construction boundary for configured PydanticAI models."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypeVar

from openbiliclaw.ai.providers.models.config import ModelInstanceConfig, ProviderKind
from openbiliclaw.ai.providers.verification import (
    UnsupportedCapabilityError,
    VerifiedCapabilities,
)

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from openbiliclaw.ai.runtime.capabilities import ModelCapabilities

T = TypeVar("T")


class VaultResolver(Protocol):
    def resolve(self, secret_id: str, callback: Callable[[memoryview], T]) -> T: ...


ModelBuilder = Callable[[ModelInstanceConfig, str], "Model"]


@dataclass(frozen=True, slots=True)
class BuiltModel:
    """Constructed model plus stable, non-secret ownership and capability metadata."""

    model: Model = field(repr=False)
    instance_id: str
    provider: str
    owner: str
    declared_capabilities: ModelCapabilities
    verification: VerifiedCapabilities


class ModelFactory:
    """One explicit construction branch per supported provider kind."""

    def __init__(
        self,
        vault: VaultResolver,
        *,
        builders: Mapping[ProviderKind, ModelBuilder] | None = None,
    ) -> None:
        self._vault = vault
        self._builders = dict(builders or {})

    def build(self, config: ModelInstanceConfig) -> BuiltModel:
        """Resolve a credential only inside its trusted provider constructor."""

        if config.provider is ProviderKind.DASHSCOPE:
            raise UnsupportedCapabilityError(
                "dashscope chat is not natively supported by PydanticAI"
            )
        builder = self._builders.get(config.provider) or self._native_builder(config.provider)
        if config.secret_ref is None:
            model = builder(config, "")
        else:
            model = self._vault.resolve(
                config.secret_ref,
                lambda secret: builder(config, secret.tobytes().decode("utf-8")),
            )
        fingerprint = config.fingerprint()
        return BuiltModel(
            model=model,
            instance_id=f"{config.provider.value}:{config.model_name}:{fingerprint[:12]}",
            provider=config.provider.value,
            owner=config.owner,
            declared_capabilities=config.capabilities,
            verification=VerifiedCapabilities.unverified(config),
        )

    @staticmethod
    def _native_builder(provider: ProviderKind) -> ModelBuilder:
        if provider is ProviderKind.OPENAI:
            from .openai import build as build_openai

            return lambda config, key: build_openai(config, key)
        if provider is ProviderKind.ANTHROPIC:
            from .anthropic import build as build_anthropic

            return lambda config, key: build_anthropic(config, key)
        if provider is ProviderKind.GOOGLE:
            from .google import build as build_google

            return lambda config, key: build_google(config, key)
        if provider is ProviderKind.OLLAMA:
            from .ollama import build as build_ollama

            return lambda config, key: build_ollama(config, key)
        if provider is ProviderKind.OPENROUTER:
            from .openrouter import build as build_openrouter

            return lambda config, key: build_openrouter(config, key)
        raise UnsupportedCapabilityError(f"{provider.value} chat is unsupported")
