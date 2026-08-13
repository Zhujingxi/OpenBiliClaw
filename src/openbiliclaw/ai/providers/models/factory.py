"""Single trusted construction boundary for native PydanticAI providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypeVar

from openbiliclaw.ai.providers.catalog import Protocol as ProviderProtocol
from openbiliclaw.ai.providers.catalog import UnsupportedProtocolError
from openbiliclaw.ai.providers.models.config import ModelInstanceConfig
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
    """Dispatch catalog protocols through PydanticAI's provider registry."""

    def __init__(
        self,
        vault: VaultResolver,
        *,
        builders: Mapping[ProviderProtocol, ModelBuilder] | None = None,
    ) -> None:
        self._vault = vault
        self._builders = dict(builders or {})

    def build(self, config: ModelInstanceConfig) -> BuiltModel:
        """Resolve a secret only inside its trusted native constructor."""

        builder = self._builders.get(config.protocol) or self._native_builder(config)
        model = self._vault.resolve(
            config.secret_ref,
            lambda secret: builder(config, secret.tobytes().decode("utf-8")),
        )
        fingerprint = config.fingerprint()
        return BuiltModel(
            model=model,
            instance_id=f"{config.provider}:{config.model_name}:{fingerprint[:12]}",
            provider=config.provider,
            owner=config.owner,
            declared_capabilities=config.capabilities,
            verification=VerifiedCapabilities.unverified(config),
        )

    @staticmethod
    def _native_builder(config: ModelInstanceConfig) -> ModelBuilder:
        if config.protocol == "anthropic":
            from .anthropic import build as anthropic_build

            return anthropic_build
        if config.protocol == "google":
            from .google import build as google_build

            return google_build
        if config.protocol == "openrouter":
            from .openrouter import build as openrouter_build

            return openrouter_build
        if config.protocol == "openai":
            from .openai import build as openai_build

            return openai_build
        raise UnsupportedProtocolError(f"unsupported provider protocol: {config.protocol}")
