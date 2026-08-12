"""Explicit duplicate-safe provider registry; no runtime import scanning."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .capabilities import (
    ActionCapability,
    CreatorCapability,
    FeedCapability,
    FetchCapability,
    HistoryCapability,
    ObservationCapability,
    ProjectionCapability,
    RelatedCapability,
    SavedCapability,
    SearchCapability,
)
from .errors import ContentIntegrationError, IntegrationErrorCode
from .manifest import CapabilityKind

if TYPE_CHECKING:
    from .identity import ProviderId
    from .manifest import ProviderManifest

_CAPABILITY_PROTOCOLS: tuple[tuple[CapabilityKind, type[object]], ...] = (
    (CapabilityKind.SEARCH, SearchCapability),
    (CapabilityKind.FEED, FeedCapability),
    (CapabilityKind.FETCH, FetchCapability),
    (CapabilityKind.RELATED, RelatedCapability),
    (CapabilityKind.CREATOR, CreatorCapability),
    (CapabilityKind.HISTORY, HistoryCapability),
    (CapabilityKind.SAVED, SavedCapability),
    (CapabilityKind.ACTION, ActionCapability),
    (CapabilityKind.PROJECTION, ProjectionCapability),
    (CapabilityKind.OBSERVATION, ObservationCapability),
)


def provider_contract_violations(
    manifest: ProviderManifest, implementation: object
) -> tuple[str, ...]:
    """Return deterministic advertised-capability contract failures."""

    return tuple(
        f"advertised {kind.value} capability is not implemented"
        for kind, protocol in _CAPABILITY_PROTOCOLS
        if kind in manifest.capabilities and not isinstance(implementation, protocol)
    )


class ContentProviderRegistry:
    """Registry populated explicitly by Composition with provider instances."""

    def __init__(self) -> None:
        self._providers: dict[ProviderId, tuple[ProviderManifest, object]] = {}

    def register(self, manifest: ProviderManifest, implementation: object) -> None:
        if manifest.provider_id in self._providers:
            raise ContentIntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE,
                "provider is already registered",
            )
        violations = provider_contract_violations(manifest, implementation)
        if violations:
            raise ContentIntegrationError(
                IntegrationErrorCode.UNAVAILABLE_CAPABILITY,
                violations[0],
            )
        self._providers[manifest.provider_id] = (manifest, implementation)

    def provider(self, provider_id: ProviderId) -> object:
        registration = self._providers.get(provider_id)
        if registration is None:
            raise ContentIntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE,
                "provider is not registered",
            )
        return registration[1]

    def manifest(self, provider_id: ProviderId) -> ProviderManifest:
        registration = self._providers.get(provider_id)
        if registration is None:
            raise ContentIntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE,
                "provider is not registered",
            )
        return registration[0]

    def manifests(self) -> tuple[ProviderManifest, ...]:
        return tuple(
            registration[0]
            for _provider_id, registration in sorted(
                self._providers.items(), key=lambda item: item[0].value
            )
        )
