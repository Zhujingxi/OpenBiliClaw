"""Single trusted construction boundary for native PydanticAI providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypeVar

from openbiliclaw.ai.providers.models.config import ModelInstanceConfig, ProviderKind
from openbiliclaw.ai.providers.verification import VerifiedCapabilities

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from openbiliclaw.ai.runtime.capabilities import ModelCapabilities

T = TypeVar("T")


class VaultResolver(Protocol):
    def resolve(self, secret_id: str, callback: Callable[[memoryview], T]) -> T: ...


ModelBuilder = Callable[[ModelInstanceConfig, str], "Model"]


@dataclass(frozen=True, slots=True)
class BuiltModel:
    """Native model plus stable non-secret ownership and capability metadata."""

    model: Model = field(repr=False)
    instance_id: str
    provider: str
    owner: str
    declared_capabilities: ModelCapabilities
    verification: VerifiedCapabilities


class ModelFactory:
    """Map one configuration shape to PydanticAI's native provider layer."""

    def __init__(
        self,
        vault: VaultResolver,
        *,
        builders: Mapping[ProviderKind, ModelBuilder] | None = None,
    ) -> None:
        self._vault = vault
        self._builders = dict(builders or {})

    def build(self, config: ModelInstanceConfig) -> BuiltModel:
        """Resolve a secret only inside its trusted native constructor."""

        builder = self._builders.get(config.provider) or self._native_builder(config.provider)
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
            from .openai import build as native_build

            return native_build
        if provider is ProviderKind.ANTHROPIC:
            from .anthropic import build as anthropic_build

            return anthropic_build
        if provider is ProviderKind.DEEPSEEK:
            from .deepseek import build as deepseek_build

            return deepseek_build
        if provider is ProviderKind.GOOGLE:
            from .google import build as google_build

            return google_build
        from .openrouter import build as openrouter_build

        return openrouter_build
