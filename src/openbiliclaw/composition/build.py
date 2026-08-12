"""Pure-first construction of the production component graph."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from openbiliclaw.access.anonymous import (
    AnonymousAccessMethod,
    AnonymousProbeOutcome,
    AnonymousProbeResult,
)
from openbiliclaw.access.broker import AccessBroker
from openbiliclaw.access.methods import AccessMethodRegistry
from openbiliclaw.access.service import AccessService
from openbiliclaw.composition.application import (
    Application,
    ApplicationHosts,
    ApplicationServices,
    InfrastructureResources,
)
from openbiliclaw.composition.facade import CompositionFacade
from openbiliclaw.composition.lifecycle import ComponentStage, LifecyclePlan, RuntimeComponent
from openbiliclaw.composition.providers import ProviderGraph, build_providers
from openbiliclaw.composition.repositories import build_repositories
from openbiliclaw.core.config import AppSettings, SettingsOverrides, load_settings
from openbiliclaw.hosts.api.app import create_app
from openbiliclaw.hosts.api.dependencies import HostDependencies, HostSecurityPolicy
from openbiliclaw.infrastructure.credentials.keyring import keyring_or_file
from openbiliclaw.infrastructure.credentials.vault import CredentialVault
from openbiliclaw.infrastructure.events.publisher import EventPublisher
from openbiliclaw.infrastructure.http.clients import HttpClientFactory
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.infrastructure.telemetry import TelemetrySink
from openbiliclaw.observations.service import ObservationIngressService
from openbiliclaw.observations.validation import ObservationValidator
from openbiliclaw.recommendation.service import RecommendationService
from openbiliclaw.understanding.service import UnderstandingService

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.observations.events import ObservationsCommitted


@dataclass(frozen=True, slots=True)
class BuildOptions:
    data_dir: Path = Path("data-v2")
    enabled_providers: tuple[str, ...] | None = None


class _DatabaseLifecycle:
    def __init__(self, database: SqliteDatabase, migrator: SchemaMigrator) -> None:
        self._database = database
        self._migrator = migrator
        self._opened = False

    async def start(self) -> None:
        await self._migrator.migrate()
        await self._database.open()
        self._opened = True

    async def stop(self) -> None:
        await self._database.close()

    async def ready(self) -> bool:
        return self._opened and not self._database.closed


class _EventLifecycle:
    def __init__(self, publisher: EventPublisher[ObservationsCommitted]) -> None:
        self._publisher = publisher
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        await self._publisher.close()
        self._running = False

    async def ready(self) -> bool:
        return self._running


class _HttpLifecycle:
    def __init__(self, factory: HttpClientFactory) -> None:
        self._factory = factory
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        await self._factory.close()
        self._running = False

    async def ready(self) -> bool:
        return self._running


class _ProviderReadiness:
    def __init__(self, providers: ProviderGraph) -> None:
        self._providers = providers
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def ready(self) -> bool:
        return self._running and not self._providers.degraded


async def _anonymous_probe(_provider_id: str, /) -> AnonymousProbeResult:
    return AnonymousProbeResult(outcome=AnonymousProbeOutcome.AVAILABLE)


def validated_settings(
    config_path: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
    overrides: SettingsOverrides | None = None,
) -> AppSettings:
    return load_settings(config_path, environ=environ, cli_overrides=overrides)


def build_application(
    settings: AppSettings,
    *,
    options: BuildOptions | None = None,
) -> Application:
    """Construct without opening files, sockets, database handles, or model clients."""
    options = options or BuildOptions()
    data_dir = options.data_dir
    database_path = data_dir / "openbiliclaw.db"
    database = SqliteDatabase(database_path)
    events: EventPublisher[ObservationsCommitted] = EventPublisher()
    http = HttpClientFactory()
    telemetry = TelemetrySink()
    vault = CredentialVault(keyring_or_file(data_dir / "credentials.json"))
    repositories = build_repositories(database)
    provider_ids = (
        settings.content.enabled if options.enabled_providers is None else options.enabled_providers
    )
    providers = build_providers(provider_ids)
    observations = ObservationIngressService(
        repositories.observations,
        events,
        ObservationValidator(),
    )
    understanding = UnderstandingService(
        repositories.observations,
        repositories.understanding,
        analyzers=(),
        clock=lambda: datetime.now(UTC),
    )
    recommendations = RecommendationService(repositories.recommendations)
    access_registry = AccessMethodRegistry(
        (
            AnonymousAccessMethod(
                supported_providers=frozenset(providers.enabled),
                probe=_anonymous_probe,
            ),
        )
    )
    access = AccessService(AccessBroker(access_registry), access_registry, telemetry=telemetry)
    resources = InfrastructureResources(database, vault, http, events, telemetry)
    lifecycle = LifecyclePlan(
        (
            RuntimeComponent(
                "infrastructure.database",
                ComponentStage.INFRASTRUCTURE,
                _DatabaseLifecycle(database, SchemaMigrator(database_path)),
            ),
            RuntimeComponent(
                "infrastructure.events",
                ComponentStage.INFRASTRUCTURE,
                _EventLifecycle(events),
            ),
            RuntimeComponent(
                "infrastructure.http",
                ComponentStage.INFRASTRUCTURE,
                _HttpLifecycle(http),
            ),
            RuntimeComponent(
                "content.providers",
                ComponentStage.SERVICE,
                _ProviderReadiness(providers),
                optional=True,
            ),
        )
    )
    facade = CompositionFacade(
        settings=settings,
        access=access,
        provider_ids=providers.enabled,
        registry=providers.registry,
        observations=observations,
        understanding=understanding,
        recommendations=recommendations,
        health=lifecycle,
    )
    dependencies = HostDependencies(
        facade=facade,
        security=HostSecurityPolicy(bind_host=settings.host.api_host),
        lifespan=lifecycle,
    )
    return Application(
        settings=settings,
        lifecycle=lifecycle,
        resources=resources,
        repositories=repositories,
        providers=providers,
        services=ApplicationServices(
            facade=facade,
            observations=observations,
            understanding=understanding,
            recommendations=recommendations,
        ),
        hosts=ApplicationHosts(dependencies=dependencies, api=create_app(dependencies)),
    )
