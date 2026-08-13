"""Pure-first construction of the production component graph."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from openbiliclaw.access.anonymous import (
    AnonymousAccessMethod,
    AnonymousProbeOutcome,
    AnonymousProbeResult,
)
from openbiliclaw.access.broker import AccessBroker
from openbiliclaw.access.manual import ManualAccessMethod
from openbiliclaw.access.methods import AccessMethodRegistry
from openbiliclaw.access.service import AccessService
from openbiliclaw.ai.providers.embeddings import EmbeddingModelInfo, EmbeddingService
from openbiliclaw.ai.providers.embeddings.providers import build_embedding_transport
from openbiliclaw.ai.providers.embeddings.service import query_prefix_for_model
from openbiliclaw.ai.providers.models import ModelFactory, ModelInstanceConfig, ProviderKind
from openbiliclaw.ai.runtime.capabilities import ModelCapabilities
from openbiliclaw.ai.runtime.execution import AgentRunRequest, AIRuntime
from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable
from openbiliclaw.application.edit_profile import EditProfile
from openbiliclaw.application.idempotency import SqliteIdempotencyJournal
from openbiliclaw.application.pending_actions import SqlitePendingActionRepository
from openbiliclaw.application.record_feedback import RecordFeedback
from openbiliclaw.application.refresh_recommendations import RefreshRecommendations
from openbiliclaw.application.unit_of_work import FeedbackUnitOfWork, ProfileEditUnitOfWork
from openbiliclaw.assistant.agent import (
    ASSISTANT_AGENT_ID,
    ASSISTANT_REQUIREMENTS,
    build_assistant_agent,
)
from openbiliclaw.assistant.service import AssistantService
from openbiliclaw.composition.application import (
    Application,
    ApplicationHosts,
    ApplicationServices,
    InfrastructureResources,
)
from openbiliclaw.composition.assistant import AssistantController, assistant_workflow_tools
from openbiliclaw.composition.events import ObservationEventSource
from openbiliclaw.composition.facade import CompositionFacade
from openbiliclaw.composition.jobs import (
    RecommendationPipeline,
    build_recommendation_jobs,
    build_understanding_job,
)
from openbiliclaw.composition.lifecycle import ComponentStage, LifecyclePlan, RuntimeComponent
from openbiliclaw.composition.providers import ProviderGraph, build_providers
from openbiliclaw.composition.repositories import build_repositories
from openbiliclaw.composition.scheduler import ScheduledJobsLifecycle
from openbiliclaw.core.config import AppSettings, SettingsOverrides, load_settings
from openbiliclaw.core.resources import ResourceBudget
from openbiliclaw.core.supervisor import RuntimeSupervisor
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
from openbiliclaw.understanding.analyzers.contracts import PREFERENCE_ANALYZER
from openbiliclaw.understanding.analyzers.preference import (
    PreferenceDraftBatch,
    adapt_preference_drafts,
)
from openbiliclaw.understanding.service import AnalyzerContract, AnalyzerInput, UnderstandingService

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.access.methods import AccessMethod
    from openbiliclaw.core.jobs import JobDecision, JobSpec
    from openbiliclaw.observations.events import ObservationsCommitted
    from openbiliclaw.understanding.proposals import ProposalBatch


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


class _RuntimeAnalyzer:
    def __init__(self, runtime: AIRuntime) -> None:
        self._runtime = runtime
        self.contract = AnalyzerContract(
            agent_id=PREFERENCE_ANALYZER.agent_id,
            requirements=PREFERENCE_ANALYZER.requirements,
            policy=PREFERENCE_ANALYZER.policy,
            context_version=PREFERENCE_ANALYZER.context_version,
        )

    async def analyze(self, data: AnalyzerInput) -> ProposalBatch:
        now = datetime.now(UTC)
        result = await self._runtime.run(
            AgentRunRequest(
                agent_id=self.contract.agent_id,
                agent=PREFERENCE_ANALYZER.agent,
                deps=None,
                user_input=data.model_dump_json(),
                history=(),
                context=(),
                requirements=self.contract.requirements,
                policy=self.contract.policy,
                workflow="understanding.preference",
            )
        )
        output = result.output
        if not isinstance(output, PreferenceDraftBatch):
            raise TypeError("preference analyzer returned an unexpected output type")
        return adapt_preference_drafts(
            output,
            data.evidence,
            self.contract.agent_id.value,
            now,
        )


class _RefreshSupervisor:
    def __init__(
        self,
        supervisor: RuntimeSupervisor,
        jobs: tuple[JobSpec, ...],
        pipeline: RecommendationPipeline,
    ) -> None:
        self._supervisor = supervisor
        self._jobs = {job.job_id: job for job in jobs}
        self._pipeline = pipeline

    def trigger(self, job_id: str, *, maximum_items: int) -> JobDecision:
        job = self._jobs[job_id]
        if job_id != "recommendation.replenishment":
            return self._supervisor.trigger(job)

        async def replenish() -> None:
            await self._pipeline.replenish(maximum_items)

        return self._supervisor.trigger(replace(job, run=replenish))


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
    providers = build_providers(provider_ids, vault)
    observations = ObservationIngressService(
        repositories.observations,
        events,
        ObservationValidator(),
    )
    recommendations = RecommendationService(repositories.recommendations)
    access_methods: list[AccessMethod] = [
        AnonymousAccessMethod(
            supported_providers=frozenset(providers.enabled),
            probe=_anonymous_probe,
        )
    ]
    if providers.manual_specs:
        access_methods.append(ManualAccessMethod(vault, providers.manual_specs))
    access_registry = AccessMethodRegistry(tuple(access_methods))
    access = AccessService(AccessBroker(access_registry), access_registry, telemetry=telemetry)
    embeddings = None
    if settings.embedding.model_name:
        embedding_config = ModelInstanceConfig(
            provider=ProviderKind(settings.embedding.provider),
            model_name=settings.embedding.model_name,
            endpoint=settings.embedding.endpoint,
            secret_ref=settings.embedding.secret_ref.removeprefix("vault:")
            if settings.embedding.secret_ref
            else "",
            owner="embeddings",
        )
        embeddings = EmbeddingService(
            build_embedding_transport(
                embedding_config,
                vault,
                output_dimensions=settings.embedding.output_dimensions,
            ),
            EmbeddingModelInfo(
                provider=embedding_config.provider.value,
                model=embedding_config.model_name,
                dimensions=settings.embedding.output_dimensions,
                normalized=True,
                version=embedding_config.provider_version,
            ),
            ResourceBudget("embedding", settings.runtime.default_resource_limit),
            timeout_seconds=settings.runtime.default_timeout_seconds,
            query_prefix=query_prefix_for_model(embedding_config.model_name),
        )
    assistant_runtime = None
    if settings.model.model_name:
        configured = ModelFactory(vault).build(
            ModelInstanceConfig(
                provider=ProviderKind(settings.model.provider),
                model_name=settings.model.model_name,
                endpoint=settings.model.endpoint,
                secret_ref=settings.model.secret_ref.removeprefix("vault:")
                if settings.model.secret_ref
                else "",
                capabilities=ModelCapabilities(
                    tools=True, structured_output=True, context_tokens=8_192
                ),
                owner="assistant",
            )
        )
        routed_model = ConfiguredModel(
            instance_id=configured.instance_id,
            provider=configured.provider,
            model=configured.model,
            capabilities=configured.declared_capabilities,
        )
        assistant_runtime = AIRuntime(
            RouteTable(
                (
                    ModelRoute(ASSISTANT_AGENT_ID, ASSISTANT_REQUIREMENTS, (routed_model,)),
                    ModelRoute(
                        PREFERENCE_ANALYZER.agent_id,
                        PREFERENCE_ANALYZER.requirements,
                        (routed_model,),
                    ),
                )
            ),
            ResourceBudget("model", settings.runtime.default_resource_limit),
        )
    analyzers = (_RuntimeAnalyzer(assistant_runtime),) if assistant_runtime is not None else ()
    understanding = UnderstandingService(
        repositories.observations,
        repositories.understanding,
        analyzers=analyzers,
        clock=lambda: datetime.now(UTC),
    )
    pipeline = RecommendationPipeline(
        providers,
        access,
        repositories,
        understanding,
        target_count=settings.recommendation.pool_target_count,
    )
    jobs = build_recommendation_jobs(pipeline)
    if assistant_runtime is not None:
        jobs = (*jobs, build_understanding_job(understanding))
    supervisor = RuntimeSupervisor(
        {
            "network": ResourceBudget("network", settings.runtime.default_resource_limit),
            "model": ResourceBudget("model", settings.runtime.default_resource_limit),
            "database": ResourceBudget("database", 1),
        },
        shutdown_grace_seconds=settings.runtime.default_timeout_seconds,
    )
    resources = InfrastructureResources(database, vault, http, events, telemetry)
    host_events = ObservationEventSource(events)
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
                "host.events",
                ComponentStage.SERVICE,
                host_events,
            ),
            RuntimeComponent(
                "content.providers",
                ComponentStage.SERVICE,
                _ProviderReadiness(providers),
                optional=True,
            ),
            RuntimeComponent(
                "core.jobs",
                ComponentStage.CORE_JOBS,
                ScheduledJobsLifecycle(supervisor, jobs),
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
        health=supervisor,
        idempotency=SqliteIdempotencyJournal(database),
        refresh=RefreshRecommendations(_RefreshSupervisor(supervisor, jobs, pipeline)),
        feedback=RecordFeedback(
            FeedbackUnitOfWork(repositories.recommendations, repositories.observations),
            clock=lambda: datetime.now(UTC),
        ),
        profile_edit=EditProfile(
            ProfileEditUnitOfWork(repositories.understanding, repositories.observations),
            clock=lambda: datetime.now(UTC),
        ),
        pending_actions=SqlitePendingActionRepository(database),
    )
    assistant = None
    if assistant_runtime is not None:
        assistant = AssistantService(
            assistant_runtime, build_assistant_agent(assistant_workflow_tools(facade))
        )
        controller = AssistantController(
            assistant, repositories.conversations, understanding, facade
        )
        facade.set_assistant(controller)
    dependencies = HostDependencies(
        facade=facade,
        security=HostSecurityPolicy(
            bind_host=settings.host.api_host,
            allowed_origins=(
                f"http://localhost:{settings.host.api_port}",
                f"http://127.0.0.1:{settings.host.api_port}",
            ),
        ),
        events=host_events,
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
            assistant=assistant,
            embeddings=embeddings,
        ),
        hosts=ApplicationHosts(dependencies=dependencies, api=create_app(dependencies)),
    )
