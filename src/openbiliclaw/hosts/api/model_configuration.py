"""Host boundary for catalog browsing and write-only model configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Protocol as TypingProtocol

from openbiliclaw.ai.providers.catalog import (
    CapabilityConfig,
    CatalogError,
    ModelCatalog,
    ProviderCatalog,
    resolve_model,
)
from openbiliclaw.ai.providers.catalog import (
    Protocol as ModelProtocol,
)
from openbiliclaw.core.config import AppSettings, CapabilitySettings, ModelOptions, ModelSettings
from openbiliclaw.core.config_writer import write_model_settings

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.infrastructure.credentials.vault import CredentialVault


class ModelConfigurationError(RuntimeError):
    """A safe model configuration operation failed."""


@dataclass(frozen=True, slots=True)
class ModelUpdate:
    provider: str
    model_name: str
    endpoint: str | None
    protocol: ModelProtocol | None
    capabilities: CapabilitySettings | None
    api_key: str


class ModelConfiguration(TypingProtocol):
    def catalog(self) -> ProviderCatalog: ...
    def settings(self) -> AppSettings: ...
    def update(self, change: ModelUpdate) -> AppSettings: ...


class FileModelConfiguration:
    """Persist one model section and secret bytes without exposing either reference."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        config_path: Path | None,
        catalog: ModelCatalog,
        vault: CredentialVault,
    ) -> None:
        self._settings = settings
        self._config_path = config_path
        self._catalog = catalog
        self._vault = vault

    def catalog(self) -> ProviderCatalog:
        return self._catalog.load()

    def settings(self) -> AppSettings:
        return self._settings

    def update(self, change: ModelUpdate) -> AppSettings:
        if self._config_path is None:
            raise ModelConfigurationError("model configuration requires a writable config path")
        catalog = self.catalog()
        try:
            capability_config = (
                CapabilityConfig.model_validate(change.capabilities.model_dump())
                if change.capabilities is not None
                else None
            )
            resolved = resolve_model(
                catalog,
                provider_id=change.provider,
                model_name=change.model_name,
                endpoint=change.endpoint,
                protocol=change.protocol,
                capabilities=capability_config,
            )
        except (CatalogError, ValueError) as error:
            raise ModelConfigurationError("model configuration is invalid") from error
        secret_ref = self._settings.model.secret_ref
        old_secret_id = secret_ref.removeprefix("vault:") if secret_ref is not None else None
        new_secret_id: str | None = None
        if change.api_key:
            new_secret_id = self._vault.store(change.api_key.encode("utf-8"))
            secret_ref = f"vault:{new_secret_id}"
        model = ModelSettings(
            provider=change.provider,
            model_name=change.model_name,
            protocol=(resolved.protocol if change.provider not in catalog.root else None),
            endpoint=change.endpoint,
            secret_ref=secret_ref,
            capabilities=(change.capabilities if change.provider not in catalog.root else None),
            options=ModelOptions(),
        )
        replacement = self._settings.model_copy(update={"model": model})
        try:
            write_model_settings(self._config_path, model)
        except OSError as error:
            if new_secret_id is not None:
                self._vault.delete(new_secret_id)
            raise ModelConfigurationError("model configuration could not be persisted") from error
        if new_secret_id is not None and old_secret_id is not None:
            self._vault.delete(old_secret_id)
        self._settings = replacement
        return replacement
