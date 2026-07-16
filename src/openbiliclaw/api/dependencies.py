"""Typed dependency container and access-control boundary for the vNext API."""

from __future__ import annotations

import inspect
import os
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Protocol, cast

from alembic import command
from alembic.config import Config
from fastapi import Depends, HTTPException, Request, status

from openbiliclaw.features.activity.service import ActivityService
from openbiliclaw.features.chat.service import ChatService
from openbiliclaw.features.feed.service import FeedbackService, FeedPolicy, FeedService
from openbiliclaw.features.library.service import LibraryService
from openbiliclaw.features.profile.service import ProfileService
from openbiliclaw.features.sources.service import SourceAccountService, SourceTaskService
from openbiliclaw.features.system.domain import DatabaseSettings
from openbiliclaw.features.system.service import OnboardingService, SettingsService
from openbiliclaw.infrastructure.ai.health import (
    ALIASES,
    AIHealthResult,
    AIHealthService,
    AliasHealth,
)
from openbiliclaw.infrastructure.ai.runner import LiteLLMModelResolver, TaskRunner
from openbiliclaw.infrastructure.ai.use_cases import (
    TaskRunnerBatchAssessor,
    TaskRunnerChatResponder,
    TaskRunnerProfileDeltaAI,
    TransactionalAIRunRecorder,
)
from openbiliclaw.infrastructure.database.base import create_engine_and_session
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs.source_composition import build_default_source_registry
from openbiliclaw.infrastructure.jobs.tasks import HueyJobQueue, JobService
from openbiliclaw.infrastructure.security.credentials import CredentialCipher

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
    from uuid import UUID

    from openbiliclaw.features.activity.domain import ActivityEvent, ProfileSignal
    from openbiliclaw.features.chat.service import ChatChunk
    from openbiliclaw.features.feed.domain import FeedItem, Interaction
    from openbiliclaw.features.library.domain import CollectionItem, CollectionKind
    from openbiliclaw.features.profile.domain import ProfileSnapshot
    from openbiliclaw.features.sources.domain import (
        ClaimedSourceTask,
        SourceAccountStatus,
        SourceId,
        SourceTaskCompletion,
    )
    from openbiliclaw.features.system.domain import UserSettings
    from openbiliclaw.infrastructure.jobs.tasks import JobRunSnapshot

ACCESS_TOKEN_ENV = "OPENBILICLAW_ACCESS_TOKEN"


class DependencyUnavailableError(RuntimeError):
    """A configured infrastructure dependency is currently unavailable."""


class SettingsPort(Protocol):
    def get(self) -> UserSettings: ...

    def update(self, patch: Mapping[str, object]) -> UserSettings: ...


class OnboardingPort(Protocol):
    def status(self) -> UserSettings: ...

    def start(self, source_ids: tuple[str, ...]) -> object: ...


class SourcesPort(Protocol):
    def manifests(self) -> tuple[object, ...]: ...

    def statuses(self) -> tuple[SourceAccountStatus, ...]: ...

    def configure(
        self, source_id: SourceId, account_key: str, credentials: Mapping[str, object]
    ) -> SourceAccountStatus: ...


class SourceTasksPort(Protocol):
    def claim(self, source_id: str) -> ClaimedSourceTask | None: ...

    def complete(
        self, task_id: UUID, lease_token: str, result: Mapping[str, object]
    ) -> SourceTaskCompletion: ...


class ActivityPort(Protocol):
    def ingest(self, event: ActivityEvent) -> tuple[ProfileSignal, ...]: ...


class ProfilePort(Protocol):
    def current(self) -> ProfileSnapshot | None: ...


class FeedPort(Protocol):
    def list_entries(self, *, limit: int, offset: int) -> tuple[FeedItem, ...]: ...


class FeedbackPort(Protocol):
    def record(self, interaction: Interaction) -> ProfileSignal: ...


class LibraryPort(Protocol):
    def list(self, collection: CollectionKind) -> tuple[CollectionItem, ...]: ...

    def save(
        self, collection: CollectionKind, content_id: UUID, *, note: str = ""
    ) -> CollectionItem: ...

    def remove(self, collection: CollectionKind, content_id: UUID) -> bool: ...


class ChatPort(Protocol):
    def stream(
        self, *, conversation_id: UUID, message: str, learn: bool = False
    ) -> AsyncIterator[ChatChunk]: ...


class JobsPort(Protocol):
    def schedule(
        self, job_name: str, *, idempotency_key: str, priority: int | None = None
    ) -> JobRunSnapshot: ...

    def inspect(self, run_id: UUID) -> JobRunSnapshot: ...

    def list(self, *, limit: int = 100) -> tuple[JobRunSnapshot, ...]: ...

    def cancel(self, run_id: UUID) -> JobRunSnapshot: ...


class AIHealthPort(Protocol):
    async def check_aliases(self) -> AIHealthResult: ...


class _UnavailableAssessor:
    async def assess_batch(
        self, profile: object, content: tuple[object, ...]
    ) -> tuple[object, ...]:
        raise DependencyUnavailableError("AI analysis is not configured")


class _UnavailableResponder:
    async def respond(self, *, conversation_id: object, message: str) -> str:
        raise DependencyUnavailableError("interactive AI is not configured")


class _DeferredCredentialCipher:
    def encrypt(self, plaintext: str) -> object:
        return CredentialCipher.from_environment().encrypt(plaintext)


class _UnavailableAIHealth:
    async def check_aliases(self) -> AIHealthResult:
        return AIHealthResult(
            proxy_reachable=False,
            aliases=tuple(
                AliasHealth(
                    alias=alias,
                    available=False,
                    state="unavailable",
                    reason="proxy_credentials_missing",
                )
                for alias in ALIASES
            ),
        )


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """Bearer-token policy populated only from installer/runtime configuration."""

    token: str | None = None

    @classmethod
    def from_environment(cls) -> AccessPolicy:
        token = os.getenv(ACCESS_TOKEN_ENV)
        return cls(token=token if token else None)

    def authorize(self, authorization: str | None) -> None:
        if not self.token:
            raise DependencyUnavailableError("API access token is not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="bearer authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        candidate = authorization.removeprefix("Bearer ")
        if not secrets.compare_digest(candidate.encode(), self.token.encode()):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="access denied")


@dataclass(slots=True)
class ApplicationContainer:
    """Application services injected into transport routers at composition time."""

    access: AccessPolicy
    settings: SettingsPort
    onboarding: OnboardingPort
    sources: SourcesPort
    source_tasks: SourceTasksPort
    activity: ActivityPort
    profile: ProfilePort
    feed: FeedPort
    feedback: FeedbackPort
    library: LibraryPort
    chat: ChatPort
    jobs: JobsPort
    ai_health: AIHealthPort
    startup_hook: Callable[[], object] | None = field(default=None, repr=False)
    shutdown_hook: Callable[[], object] | None = field(default=None, repr=False)

    async def startup(self) -> None:
        await _maybe_await(self.startup_hook)

    async def shutdown(self) -> None:
        await _maybe_await(self.shutdown_hook)


async def _maybe_await(callback: Callable[[], object] | None) -> None:
    if callback is None:
        return
    result = callback()
    if inspect.isawaitable(result):
        await cast("Awaitable[object]", result)


def build_application_container() -> ApplicationContainer:
    """Compose lazy production adapters without contacting external services."""

    database_settings = DatabaseSettings()
    engine, session_factory = create_engine_and_session(database_settings)

    def uow_factory() -> UnitOfWork:
        return UnitOfWork(session_factory)

    registry = build_default_source_registry(session_factory)
    settings = SettingsService(cast("Callable[[], Any]", uow_factory))
    source_tasks = SourceTaskService(cast("Callable[[], Any]", uow_factory), registry)
    sources = SourceAccountService(
        cast("Callable[[], Any]", uow_factory),
        cipher=_DeferredCredentialCipher(),
        registry=registry,
    )
    runner, resolver = _build_task_runner(uow_factory)
    profile = ProfileService(
        cast("Callable[[], Any]", uow_factory),
        ai=TaskRunnerProfileDeltaAI(runner) if runner else None,
    )
    feed = FeedService(
        cast("Callable[[], Any]", uow_factory),
        connectors=registry.connectors,
        assessor=TaskRunnerBatchAssessor(runner) if runner else cast("Any", _UnavailableAssessor()),
        policy=FeedPolicy(),
        settings=settings,
    )
    chat = ChatService(
        cast("Callable[[], Any]", uow_factory),
        responder=(
            TaskRunnerChatResponder(runner) if runner else cast("Any", _UnavailableResponder())
        ),
    )
    jobs = JobService(
        cast("Callable[[], Any]", uow_factory),
        queue=HueyJobQueue(),
        source_sync_interval_minutes=lambda: settings.get().source_sync_interval_minutes,
    )
    ai_health, health_client = _build_ai_health()

    def startup() -> None:
        config = Config(os.getenv("OPENBILICLAW_ALEMBIC_INI", "alembic.ini"))
        config.set_main_option("sqlalchemy.url", database_settings.url)
        command.upgrade(config, "head")
        jobs.recover_interrupted()

    async def shutdown() -> None:
        if resolver is not None:
            await resolver.aclose()
        if health_client is not None:
            await health_client.aclose()
        engine.dispose()

    return ApplicationContainer(
        access=AccessPolicy.from_environment(),
        settings=settings,
        onboarding=OnboardingService(settings, jobs),
        sources=sources,
        source_tasks=source_tasks,
        activity=ActivityService(cast("Callable[[], Any]", uow_factory)),
        profile=profile,
        feed=feed,
        feedback=FeedbackService(cast("Callable[[], Any]", uow_factory)),
        library=LibraryService(cast("Callable[[], Any]", uow_factory)),
        chat=chat,
        jobs=jobs,
        ai_health=ai_health,
        startup_hook=startup,
        shutdown_hook=shutdown,
    )


def _build_task_runner(
    uow_factory: Callable[[], UnitOfWork],
) -> tuple[TaskRunner | None, LiteLLMModelResolver | None]:
    api_key = os.getenv("OPENBILICLAW_LITELLM_API_KEY")
    if not api_key:
        return None, None
    resolver = LiteLLMModelResolver(
        base_url=os.getenv("OPENBILICLAW_LITELLM_BASE_URL", "http://127.0.0.1:4000"),
        api_key=api_key,
    )
    recorder = TransactionalAIRunRecorder(uow_factory)
    return TaskRunner(model_resolver=resolver, recorder=recorder), resolver


def _build_ai_health() -> tuple[Any, AIHealthService | None]:
    api_key = os.getenv("OPENBILICLAW_LITELLM_API_KEY")
    if not api_key:
        return _UnavailableAIHealth(), None
    service = AIHealthService(
        base_url=os.getenv("OPENBILICLAW_LITELLM_BASE_URL", "http://127.0.0.1:4000"),
        api_key=api_key,
    )
    return service, service


def get_container(request: Request) -> ApplicationContainer:
    return cast("ApplicationContainer", request.app.state.container)


Container = Annotated[ApplicationContainer, Depends(get_container)]


def require_access(
    request: Request,
    container: Container,
) -> None:
    container.access.authorize(request.headers.get("Authorization"))


def require_onboarding_access(
    request: Request,
    container: Container,
) -> None:
    if not container.settings.get().onboarding_complete:
        return
    container.access.authorize(request.headers.get("Authorization"))


__all__ = [
    "ACCESS_TOKEN_ENV",
    "AccessPolicy",
    "ApplicationContainer",
    "Container",
    "DependencyUnavailableError",
    "build_application_container",
    "get_container",
    "require_access",
    "require_onboarding_access",
]
