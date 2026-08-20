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
from openbiliclaw.ai.providers.catalog import CapabilityConfig, ModelCatalog, resolve_model
from openbiliclaw.ai.providers.embeddings import EmbeddingModelInfo, EmbeddingService
from openbiliclaw.ai.providers.embeddings.index import EmbeddingIndex
from openbiliclaw.ai.providers.embeddings.providers import build_embedding_transport
from openbiliclaw.ai.providers.embeddings.service import query_prefix_for_model
from openbiliclaw.ai.providers.models import ModelFactory, ModelInstanceConfig, ModelOptions
from openbiliclaw.ai.runtime.budgets import PolicyBook
from openbiliclaw.ai.runtime.execution import AgentRunRequest, AIRuntime
from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable
from openbiliclaw.application.edit_profile import EditProfile
from openbiliclaw.application.external_evidence import (
    ExternalEvidenceIngestion,
    build_external_evidence_job,
)
from openbiliclaw.application.idempotency import SqliteIdempotencyJournal
from openbiliclaw.application.pending_actions import SqlitePendingActionRepository
from openbiliclaw.application.record_feedback import RecordFeedback, RecordFeedbackForShown
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
from openbiliclaw.composition.external_evidence import youtube_takeout_import
from openbiliclaw.composition.facade import CompositionFacade
from openbiliclaw.composition.jobs import (
    DEFAULT_PROFILE_ID,
    RecommendationPipeline,
    build_recommendation_jobs,
    build_understanding_job,
)
from openbiliclaw.composition.lifecycle import ComponentStage, LifecyclePlan, RuntimeComponent
from openbiliclaw.composition.providers import ProviderGraph, build_providers
from openbiliclaw.composition.repositories import build_repositories
from openbiliclaw.composition.scheduler import ScheduledJobsLifecycle
from openbiliclaw.content.integration.capabilities import FetchCapability, ProjectionCapability
from openbiliclaw.core.config import AppSettings, SettingsOverrides, load_settings
from openbiliclaw.core.resources import ResourceBudget
from openbiliclaw.core.supervisor import RuntimeSupervisor
from openbiliclaw.hosts.api.app import create_app
from openbiliclaw.hosts.api.auth import AuthTokenService, SqliteAuthTokenRepository
from openbiliclaw.hosts.api.dependencies import HostDependencies, HostSecurityPolicy
from openbiliclaw.hosts.api.media_proxy import MediaProxy
from openbiliclaw.hosts.api.model_configuration import FileModelConfiguration
from openbiliclaw.infrastructure.credentials.keyring import keyring_or_file
from openbiliclaw.infrastructure.credentials.vault import CredentialVault
from openbiliclaw.infrastructure.events.publisher import EventPublisher
from openbiliclaw.infrastructure.http.clients import HttpClientFactory
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.infrastructure.telemetry import TelemetrySink
from openbiliclaw.observations.service import ObservationIngressService
from openbiliclaw.observations.validation import ObservationValidator
from openbiliclaw.recommendation.brief import BriefCompiler, BriefService
from openbiliclaw.recommendation.brief_agent import BRIEF_AGENT
from openbiliclaw.recommendation.hypotheses import HypothesisRegistry
from openbiliclaw.recommendation.inspection import (
    INSPECTION_AGENT,
    FrameAcquirer,
    InspectionService,
)
from openbiliclaw.recommendation.models import Candidate, FeedbackKind, record_identity
from openbiliclaw.recommendation.policy_journal import SqlitePolicyJournal
from openbiliclaw.recommendation.rewards import record_supply_reward
from openbiliclaw.recommendation.service import RecommendationService
from openbiliclaw.understanding.analyzers.contracts import PREFERENCE_ANALYZER
from openbiliclaw.understanding.analyzers.preference import (
    PreferenceDraftBatch,
    adapt_preference_drafts,
)
from openbiliclaw.understanding.evidence import EvidenceLink
from openbiliclaw.understanding.profile import EmergingInterestClaim, claim_id
from openbiliclaw.understanding.proposals import ClaimProposal, ProposalOwner
from openbiliclaw.understanding.resynthesis import ResynthesisService
from openbiliclaw.understanding.service import AnalyzerContract, AnalyzerInput, UnderstandingService

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.access.methods import AccessMethod
    from openbiliclaw.core.jobs import JobDecision, JobSpec
    from openbiliclaw.observations.events import ObservationsCommitted
    from openbiliclaw.observations.models import Observation
    from openbiliclaw.recommendation.models import FeedbackRecord
    from openbiliclaw.understanding.proposals import ProposalBatch


@dataclass(frozen=True, slots=True)
class BuildOptions:
    data_dir: Path = Path("data-v2")
    config_path: Path | None = None
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


class _AccessRehydration:
    def __init__(self, access: AccessService) -> None:
        self._access = access
        self._running = False

    async def start(self) -> None:
        await self._access.rehydrate()
        self._running = True

    async def stop(self) -> None:
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
    providers = build_providers(provider_ids, vault)
    media_proxy = MediaProxy(providers.registry.manifests(), http)
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
    external_evidence = ExternalEvidenceIngestion(
        providers.registry,
        access,
        observations,
        clock=lambda: datetime.now(UTC),
        importers={"youtube": youtube_takeout_import},
    )
    catalog = (
        ModelCatalog(data_dir / "models.dev.json").load() if settings.model.model_name else None
    )
    embeddings = None
    embedding_model = None
    if settings.embedding.model_name:
        embedding_config = ModelInstanceConfig(
            provider=settings.embedding.provider,
            protocol=settings.embedding.protocol or "openai",
            model_name=settings.embedding.model_name,
            endpoint=settings.embedding.endpoint,
            secret_ref=settings.embedding.secret_ref.removeprefix("vault:")
            if settings.embedding.secret_ref
            else "",
            owner="embeddings",
        )
        embedding_model = EmbeddingModelInfo(
            provider=embedding_config.provider,
            model=embedding_config.model_name,
            dimensions=settings.embedding.output_dimensions,
            normalized=True,
            version=embedding_config.provider_version,
        )
        embeddings = EmbeddingService(
            build_embedding_transport(
                embedding_config,
                vault,
                output_dimensions=settings.embedding.output_dimensions,
            ),
            embedding_model,
            ResourceBudget("embedding", settings.runtime.default_resource_limit),
            timeout_seconds=settings.runtime.default_timeout_seconds,
            query_prefix=query_prefix_for_model(embedding_config.model_name),
        )
    assistant_runtime = None
    briefs_configured = False
    vision_configured = False
    if settings.model.model_name:
        assert catalog is not None
        resolved = resolve_model(
            catalog,
            provider_id=settings.model.provider,
            model_name=settings.model.model_name,
            endpoint=settings.model.endpoint,
            protocol=settings.model.protocol,
            capabilities=CapabilityConfig.model_validate(settings.model.capabilities.model_dump())
            if settings.model.capabilities is not None
            else None,
        )
        configured = ModelFactory(vault).build(
            ModelInstanceConfig(
                provider=resolved.provider,
                protocol=resolved.protocol,
                model_name=settings.model.model_name,
                endpoint=resolved.endpoint,
                secret_ref=settings.model.secret_ref.removeprefix("vault:")
                if settings.model.secret_ref
                else "",
                options=ModelOptions(disable_thinking=settings.model.options.disable_thinking),
                capabilities=resolved.capabilities,
                owner="assistant",
            )
        )
        routed_model = ConfiguredModel(
            instance_id=configured.instance_id,
            provider=configured.provider,
            model=configured.model,
            capabilities=configured.declared_capabilities,
        )
        policy_book = PolicyBook.from_overrides(
            {
                agent: {
                    key: value
                    for key, value in policy.model_dump().items()
                    if isinstance(value, (int, float))
                }
                for agent, policy in settings.runtime.agents.items()
            }
        )
        routes = [
            ModelRoute(ASSISTANT_AGENT_ID, ASSISTANT_REQUIREMENTS, (routed_model,)),
            ModelRoute(
                PREFERENCE_ANALYZER.agent_id,
                PREFERENCE_ANALYZER.requirements,
                (routed_model,),
            ),
        ]
        briefs_configured = routed_model.capabilities.satisfies(BRIEF_AGENT.requirements)
        if briefs_configured:
            routes.append(
                ModelRoute(BRIEF_AGENT.agent_id, BRIEF_AGENT.requirements, (routed_model,))
            )
        vision_configured = routed_model.capabilities.satisfies(INSPECTION_AGENT.requirements)
        if vision_configured:
            routes.append(
                ModelRoute(
                    INSPECTION_AGENT.agent_id,
                    INSPECTION_AGENT.requirements,
                    (routed_model,),
                )
            )
        assistant_runtime = AIRuntime(
            RouteTable(tuple(routes)),
            ResourceBudget("model", settings.runtime.default_resource_limit),
            policies=policy_book,
        )
    analyzers = (_RuntimeAnalyzer(assistant_runtime),) if assistant_runtime is not None else ()

    def clock() -> datetime:
        return datetime.now(UTC)

    semantic_index = EmbeddingIndex(database, embeddings, embedding_model, clock=clock)
    resynthesis = ResynthesisService(
        repositories.understanding, clock=clock, embedding_index=semantic_index
    )
    understanding = UnderstandingService(
        repositories.observations,
        repositories.understanding,
        analyzers=analyzers,
        clock=clock,
        resynthesis=resynthesis,
        embedding_index=semantic_index,
    )
    policy_journal = SqlitePolicyJournal(database)
    hypotheses = HypothesisRegistry(policy_journal)
    briefs = (
        BriefService(
            assistant_runtime,
            hypotheses,
            policy_journal,
            BriefCompiler(providers.registry.manifests()),
        )
        if assistant_runtime is not None and briefs_configured
        else None
    )

    async def cover_source(candidate: Candidate) -> str | None:
        provider = providers.registry.provider(candidate.preview.ref.provider_id)
        handle = access.connected_handle(candidate.preview.ref.provider_id.value, None)
        if (
            handle is None
            or not isinstance(provider, FetchCapability)
            or not isinstance(provider, ProjectionCapability)
        ):
            return None
        native = await provider.fetch(candidate.preview.ref, handle)
        return provider.card_data(native).image_url

    inspections = (
        InspectionService(
            assistant_runtime,
            FrameAcquirer(media_proxy, cover=cover_source),
            policy_journal,
            semantic_index,
            clock=clock,
        )
        if assistant_runtime is not None and vision_configured
        else None
    )
    pipeline = RecommendationPipeline(
        providers,
        access,
        repositories,
        understanding,
        target_count=settings.recommendation.pool_target_count,
        hypotheses=hypotheses,
        policy_journal=policy_journal,
        briefs=briefs,
        inspections=inspections,
        semantic_index=semantic_index,
    )
    jobs = (*build_recommendation_jobs(pipeline), build_external_evidence_job(external_evidence))
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
                "access.rehydration",
                ComponentStage.SERVICE,
                _AccessRehydration(access),
            ),
            RuntimeComponent(
                "core.jobs",
                ComponentStage.CORE_JOBS,
                ScheduledJobsLifecycle(supervisor, jobs),
            ),
        )
    )

    async def record_reward(feedback: FeedbackRecord, observation: Observation) -> None:
        shown, candidate = await repositories.recommendations.reward_context(feedback.shown_id)
        # Understanding evidence first: a killed-hypothesis ValueError from the
        # ledger below must not skip the exploration interest proposal.
        if candidate.provenance.exploration is not None and feedback.kind in (
            FeedbackKind.LIKED,
            FeedbackKind.SAVED,
        ):
            evidence_id = "ev_" + observation.observation_id.removeprefix("obs_")
            topic = next(
                (item for item in candidate.topics if item.strip()), candidate.preview.title
            )[:200]
            evidence = EvidenceLink(
                evidence_id=evidence_id,
                observation_id=observation.observation_id,
                summary=(
                    f"Exploration {feedback.kind.value}: {topic}; "
                    f"arm={candidate.provenance.exploration.arm}; "
                    f"hypothesis={candidate.provenance.exploration.hypothesis_id}"
                )[:500],
                occurred_at=observation.occurred_at,
                trust=0.2,
            )
            await understanding.consider(
                DEFAULT_PROFILE_ID,
                ClaimProposal(
                    proposal_id=record_identity("prop", feedback.feedback_id),
                    analyzer_id="understanding.exploration.v1",
                    owner=ProposalOwner.TOPIC_LIFECYCLE,
                    claim=EmergingInterestClaim(
                        claim_id=claim_id("emerging_interest", topic),
                        value=topic,
                        confidence=0.2,
                        fresh_at=observation.occurred_at,
                        evidence_ids=(evidence_id,),
                    ),
                    evidence=(evidence,),
                    proposed_at=observation.occurred_at,
                ),
                evidence,
            )

        await record_supply_reward(
            hypotheses,
            feedback,
            shown,
            candidate.provenance,
            now=observation.occurred_at,
        )

    feedback = RecordFeedback(
        FeedbackUnitOfWork(repositories.recommendations, repositories.observations),
        clock=lambda: datetime.now(UTC),
        targets=repositories.recommendations,
        reward_sink=record_reward,
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
        feedback=feedback,
        feedback_for_shown=RecordFeedbackForShown(repositories.recommendations, feedback),
        external_evidence=external_evidence,
        profile_edit=EditProfile(
            ProfileEditUnitOfWork(
                repositories.understanding,
                repositories.observations,
                resynthesis=resynthesis,
                embedding_index=semantic_index,
            ),
            clock=clock,
        ),
        pending_actions=SqlitePendingActionRepository(database),
    )
    assistant = None
    if assistant_runtime is not None:
        assistant = AssistantService(
            assistant_runtime,
            build_assistant_agent(
                assistant_workflow_tools(facade),
                prompted_output=resolved.protocol == "anthropic",
            ),
        )
        controller = AssistantController(
            assistant, repositories.conversations, understanding, facade
        )
        facade.set_assistant(controller)
    bearer_token = None
    if settings.host.bearer_secret_ref is not None:
        secret_id = settings.host.bearer_secret_ref.removeprefix("vault:")
        bearer_token = vault.resolve(secret_id, lambda secret: bytes(secret).decode("utf-8"))
    dependencies = HostDependencies(
        facade=facade,
        security=HostSecurityPolicy(
            bind_host=settings.host.api_host,
            bearer_token=bearer_token,
            password_hash=settings.host.password_hash,
            allow_unauthenticated=settings.host.allow_unauthenticated,
            allowed_origins=(
                f"http://localhost:{settings.host.api_port}",
                f"http://127.0.0.1:{settings.host.api_port}",
                *settings.host.allowed_origins,
            ),
        ),
        events=host_events,
        lifespan=lifecycle,
        auth_tokens=AuthTokenService(SqliteAuthTokenRepository(database)),
        media_proxy=media_proxy,
        plugin_access=facade,
        models=FileModelConfiguration(
            settings=settings,
            config_path=options.config_path,
            catalog=ModelCatalog(data_dir / "models.dev.json"),
            vault=vault,
        ),
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
