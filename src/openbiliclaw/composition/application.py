"""Frozen top-level production application graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fastapi import FastAPI
    from typer import Typer

    from openbiliclaw.assistant.service import AssistantService
    from openbiliclaw.core.config import AppSettings
    from openbiliclaw.hosts.api.dependencies import HostDependencies, HostFacade
    from openbiliclaw.infrastructure.credentials.vault import CredentialVault
    from openbiliclaw.infrastructure.events.publisher import EventPublisher
    from openbiliclaw.infrastructure.http.clients import HttpClientFactory
    from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
    from openbiliclaw.infrastructure.telemetry import TelemetrySink
    from openbiliclaw.observations.events import ObservationsCommitted
    from openbiliclaw.observations.service import ObservationIngressService
    from openbiliclaw.recommendation.service import RecommendationService
    from openbiliclaw.understanding.service import UnderstandingService

    from .lifecycle import LifecyclePlan
    from .providers import ProviderGraph
    from .repositories import RepositoryGraph


class Startable(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class InfrastructureResources:
    """Inspectible resource ownership, not exposed to product modules."""

    database: SqliteDatabase
    vault: CredentialVault
    http: HttpClientFactory
    events: EventPublisher[ObservationsCommitted]
    telemetry: TelemetrySink


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    """Small set of top-level product boundaries."""

    facade: HostFacade | None = None
    observations: ObservationIngressService | None = None
    understanding: UnderstandingService | None = None
    recommendations: RecommendationService | None = None
    assistant: AssistantService | None = None


@dataclass(frozen=True, slots=True)
class ApplicationHosts:
    dependencies: HostDependencies | None = None
    api: FastAPI | None = None
    cli: Typer | None = None


@dataclass(frozen=True, slots=True)
class Application:
    """Concrete graph root used only by Composition and process hosts."""

    settings: AppSettings
    lifecycle: LifecyclePlan
    services: ApplicationServices = ApplicationServices()
    resources: InfrastructureResources | None = None
    repositories: RepositoryGraph | None = None
    providers: ProviderGraph | None = None
    hosts: ApplicationHosts = ApplicationHosts()

    async def start(self) -> None:
        await self.lifecycle.start()

    async def stop(self) -> None:
        await self.lifecycle.stop()

    async def ready(self) -> bool:
        return bool(self.lifecycle.active_component_ids)
