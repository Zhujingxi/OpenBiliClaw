"""Typed dependency container and access-control boundary for the vNext API."""

from __future__ import annotations

import inspect
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Protocol, cast

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer

from openbiliclaw import auth_core
from openbiliclaw.api.threading import run_sync_port
from openbiliclaw.features.activity.service import ActivityService
from openbiliclaw.features.chat.service import ChatService
from openbiliclaw.features.feed.service import FeedbackService, FeedPolicy, FeedService
from openbiliclaw.features.library.service import LibraryService
from openbiliclaw.features.profile.service import ProfileService
from openbiliclaw.features.sources.service import SourceAccountService, SourceTaskService
from openbiliclaw.features.system.domain import DatabaseSettings, UserSettings
from openbiliclaw.features.system.service import OnboardingService, SettingsService
from openbiliclaw.infrastructure.ai.health import (
    ALIASES,
    AIHealthResult,
    AIHealthService,
    AliasHealth,
    public_admin_url_from_environment,
)
from openbiliclaw.infrastructure.ai.runner import LiteLLMModelResolver, TaskRunner
from openbiliclaw.infrastructure.ai.use_cases import (
    TaskRunnerBatchAssessor,
    TaskRunnerChatResponder,
    TaskRunnerKeywordPlanner,
    TaskRunnerProfileDeltaAI,
    TaskRunnerRecommendationExplainer,
    TransactionalAIRunRecorder,
)
from openbiliclaw.infrastructure.database.base import create_engine_and_session
from openbiliclaw.infrastructure.database.operations import require_schema_at_head
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs.source_composition import build_default_source_registry
from openbiliclaw.infrastructure.jobs.tasks import HueyJobQueue, JobService
from openbiliclaw.infrastructure.security.credentials import CredentialCipher
from openbiliclaw.logging_setup import apply_owned_handler_levels
from openbiliclaw.network import set_outbound_proxy

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping
    from uuid import UUID

    from openbiliclaw.features.activity.domain import ActivityEvent, ProfileSignal
    from openbiliclaw.features.chat.service import ChatChunk, ChatHistoryPage
    from openbiliclaw.features.feed.domain import FeedItem, Interaction
    from openbiliclaw.features.library.domain import CollectionItem, CollectionKind, LibraryItem
    from openbiliclaw.features.profile.domain import ProfileEdit, ProfileSnapshot
    from openbiliclaw.features.sources.domain import (
        BrowserOperationResultValue,
        ClaimedSourceTask,
        SourceAccountDisconnectResult,
        SourceAccountStatus,
        SourceId,
        SourceManifest,
        SourceSettingsState,
        SourceTaskCompletion,
    )
    from openbiliclaw.features.sources.registry import SourceRegistry
    from openbiliclaw.features.system.domain import UserSettings
    from openbiliclaw.features.system.service import OnboardingWorkflowProgress
    from openbiliclaw.infrastructure.jobs.tasks import JobRunSnapshot

ACCESS_TOKEN_ENV = "OPENBILICLAW_ACCESS_TOKEN"
_BEARER_SCHEME = HTTPBearer(auto_error=False, scheme_name="BearerAuth")
_COOKIE_SCHEME = APIKeyCookie(
    name=auth_core.COOKIE_NAME,
    auto_error=False,
    scheme_name="SessionCookie",
)


class DependencyUnavailableError(RuntimeError):
    """A configured infrastructure dependency is currently unavailable."""


class _DeferredSourceRegistry:
    """Resolve persisted settings only after the schema guard succeeds."""

    def __init__(self, builder: Callable[[], SourceRegistry]) -> None:
        self._builder = builder
        self._ready = False

    def install(self) -> None:
        self._builder()
        self._ready = True

    def get(self) -> SourceRegistry:
        if not self._ready:
            raise DependencyUnavailableError("source registry is not initialized")
        return self._builder()


class SettingsPort(Protocol):
    def get(self) -> UserSettings: ...

    def update(self, patch: Mapping[str, object]) -> UserSettings: ...


class OnboardingPort(Protocol):
    def status(self) -> UserSettings: ...

    def start(self, source_ids: tuple[str, ...]) -> object: ...

    def progress(self, root_run_id: UUID) -> OnboardingWorkflowProgress[JobRunSnapshot]: ...


class SourcesPort(Protocol):
    def manifests(self) -> tuple[SourceManifest, ...]: ...

    def statuses(self) -> tuple[SourceAccountStatus, ...]: ...

    def settings(self, source_id: SourceId) -> SourceSettingsState: ...

    def update_settings(
        self, source_id: SourceId, patch: Mapping[str, object]
    ) -> SourceSettingsState: ...

    def configure(
        self, source_id: SourceId, account_key: str, credentials: Mapping[str, object]
    ) -> SourceAccountStatus: ...

    def disconnect(
        self, source_id: SourceId, account_key: str
    ) -> SourceAccountDisconnectResult: ...


class SourceTasksPort(Protocol):
    def claim(self, source_id: str) -> ClaimedSourceTask | None: ...

    def complete(
        self, task_id: UUID, lease_token: str, result: BrowserOperationResultValue
    ) -> SourceTaskCompletion: ...

    def fail(
        self, task_id: UUID, lease_token: str, *, code: str, error_type: str
    ) -> SourceTaskCompletion: ...


class ActivityPort(Protocol):
    def ingest(self, event: ActivityEvent) -> tuple[ProfileSignal, ...]: ...


class ProfilePort(Protocol):
    def current(self) -> ProfileSnapshot | None: ...

    def edit(self, edit: ProfileEdit) -> ProfileSnapshot: ...


class FeedPort(Protocol):
    def list_entries(self, *, limit: int, offset: int) -> tuple[FeedItem, ...]: ...


class FeedbackPort(Protocol):
    def record(self, interaction: Interaction) -> ProfileSignal | None: ...


class LibraryPort(Protocol):
    def list(self, collection: CollectionKind) -> tuple[LibraryItem, ...]: ...

    def save(
        self, collection: CollectionKind, content_id: UUID, *, note: str = ""
    ) -> CollectionItem: ...

    def remove(self, collection: CollectionKind, content_id: UUID) -> bool: ...


class ChatPort(Protocol):
    def stream(
        self, *, conversation_id: UUID, message: str, learn: bool = False
    ) -> AsyncGenerator[ChatChunk]: ...

    def history(
        self, *, conversation_id: UUID, limit: int = 50, offset: int = 0
    ) -> ChatHistoryPage: ...


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
    async def stream(
        self,
        *,
        conversation_id: object,
        message: str,
        history: tuple[object, ...],
    ) -> AsyncIterator[object]:
        del conversation_id, message, history
        raise DependencyUnavailableError("interactive AI is not configured")
        yield  # pragma: no cover - makes this an async iterator


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
            admin_url=public_admin_url_from_environment(),
        )


def _unix_time() -> int:
    return int(time.time())


@dataclass(slots=True)
class _RateLimitEntry:
    failures: list[int] = field(default_factory=list)
    locked_until: int | None = None
    in_flight: int = 0


class AuthAttemptReservation:
    """One admitted credential verification that must be finalized exactly once."""

    def __init__(self, limiter: _BoundedRateLimiter, key: str) -> None:
        self._limiter = limiter
        self._key = key
        self._active = True

    def success(self) -> None:
        self._finish("success")

    def failure(self) -> None:
        self._finish("failure")

    def release(self) -> None:
        self._finish("release")

    def _finish(self, outcome: str) -> None:
        if not self._active:
            return
        self._active = False
        self._limiter.finish(self._key, outcome)


class _BoundedRateLimiter:
    """Per-client failure limiter with deterministic expiry and bounded memory."""

    def __init__(
        self,
        *,
        max_failures: int,
        window_seconds: int,
        lockout_seconds: int,
        max_clients: int,
        clock: Callable[[], int],
    ) -> None:
        if min(max_failures, window_seconds, lockout_seconds, max_clients) < 1:
            raise ValueError("rate limit bounds must be positive")
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._lockout_seconds = lockout_seconds
        self._max_clients = max_clients
        self._clock = clock
        self._entries: OrderedDict[str, _RateLimitEntry] = OrderedDict()
        self._lock = threading.Lock()

    def begin(self, key: str) -> tuple[AuthAttemptReservation | None, int | None]:
        with self._lock:
            entry = self._entries.get(key)
            now = self._clock()
            if entry is not None and entry.locked_until is not None and entry.locked_until > now:
                self._entries.move_to_end(key)
                return None, max(1, entry.locked_until - now)
            if entry is not None and entry.locked_until is not None:
                self._entries.pop(key, None)
                entry = None
            if entry is None:
                if len(self._entries) >= self._max_clients:
                    evictable = next(
                        (
                            stored_key
                            for stored_key, stored in self._entries.items()
                            if stored.in_flight == 0
                        ),
                        None,
                    )
                    if evictable is None:
                        return None, 1
                    self._entries.pop(evictable, None)
                entry = _RateLimitEntry()
                self._entries[key] = entry
            entry.failures = [
                moment for moment in entry.failures if now - moment < self._window_seconds
            ]
            if len(entry.failures) + entry.in_flight >= self._max_failures:
                self._entries.move_to_end(key)
                return None, 1
            entry.in_flight += 1
            self._entries.move_to_end(key)
            return AuthAttemptReservation(self, key), None

    def finish(self, key: str, outcome: str) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.in_flight < 1:
                return
            entry.in_flight -= 1
            now = self._clock()
            entry.failures = [
                moment for moment in entry.failures if now - moment < self._window_seconds
            ]
            if outcome == "success":
                entry.failures.clear()
                entry.locked_until = None
            elif outcome == "failure":
                entry.failures.append(now)
                if len(entry.failures) >= self._max_failures:
                    entry.locked_until = now + self._lockout_seconds
            elif outcome != "release":
                raise ValueError("unknown authentication attempt outcome")
            if entry.in_flight == 0 and not entry.failures and entry.locked_until is None:
                self._entries.pop(key, None)
            else:
                self._entries.move_to_end(key)


@dataclass(slots=True)
class AccessPolicy:
    """Secret-safe installer, browser-session, and extension-session policy."""

    token: str | None = field(default=None, repr=False)
    password_hash: str = field(default="", repr=False)
    session_secret: str = field(default="", repr=False)
    session_ttl_hours: int = 0
    extension_access_enabled: bool = False
    extension_access_records: tuple[str, ...] = field(default=(), repr=False)
    extension_session_ttl_hours: int = 24
    clock: Callable[[], int] = field(default=_unix_time, repr=False)
    rate_limit_max_failures: int = 5
    rate_limit_window_seconds: int = 900
    rate_limit_lockout_seconds: int = 900
    rate_limit_max_clients: int = 2048
    epoch_getter: Callable[[], int] | None = field(default=None, repr=False)
    epoch_bumper: Callable[[], int] | None = field(default=None, repr=False)
    fingerprint_reconciler: Callable[[str | None], bool] | None = field(default=None, repr=False)
    _epoch: int = field(default=0, init=False, repr=False)
    _reconcile_ok: bool = field(default=True, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _login_limiter: _BoundedRateLimiter = field(init=False, repr=False)
    _extension_limiter: _BoundedRateLimiter = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._login_limiter = self._new_rate_limiter()
        self._extension_limiter = self._new_rate_limiter()
        self._reconcile_ok = self.fingerprint_reconciler is None

    def _new_rate_limiter(self) -> _BoundedRateLimiter:
        return _BoundedRateLimiter(
            max_failures=self.rate_limit_max_failures,
            window_seconds=self.rate_limit_window_seconds,
            lockout_seconds=self.rate_limit_lockout_seconds,
            max_clients=self.rate_limit_max_clients,
            clock=self.clock,
        )

    @classmethod
    def from_environment(cls) -> AccessPolicy:
        token = os.getenv(ACCESS_TOKEN_ENV)
        password_hash = os.getenv("OPENBILICLAW_WEB_PASSWORD_HASH", "")
        session_secret = os.getenv("OPENBILICLAW_SESSION_SECRET", "")
        records_raw = os.getenv("OPENBILICLAW_EXTENSION_ACCESS_KEYS", "")
        try:
            parsed_records = json.loads(records_raw) if records_raw else []
        except (TypeError, ValueError):
            parsed_records = []
        environment_records = (
            tuple(value for value in parsed_records if isinstance(value, str))
            if isinstance(parsed_records, list)
            else ()
        )
        return cls(
            token=token if token else None,
            password_hash=password_hash,
            session_secret=session_secret,
            extension_access_enabled=bool(environment_records),
            extension_access_records=environment_records,
        )

    def attach_persistence(
        self,
        *,
        epoch_getter: Callable[[], int],
        epoch_bumper: Callable[[], int],
        fingerprint_reconciler: Callable[[str | None], bool],
    ) -> None:
        """Attach vNext auth state before application startup."""

        self.epoch_getter = epoch_getter
        self.epoch_bumper = epoch_bumper
        self.fingerprint_reconciler = fingerprint_reconciler
        self._reconcile_ok = False

    def reconcile_password_fingerprint(self) -> bool:
        reconciler = self.fingerprint_reconciler
        if reconciler is None:
            self._reconcile_ok = True
            return False
        fingerprint = (
            auth_core.password_fingerprint(
                self.session_secret,
                plain=None,
                password_hash=self.password_hash,
            )
            if self.password_configured
            else None
        )
        try:
            changed = reconciler(fingerprint)
        except Exception as error:
            self._reconcile_ok = False
            raise DependencyUnavailableError(
                "authentication password state is unavailable"
            ) from error
        self._reconcile_ok = True
        return changed

    def begin_auth_attempt(
        self, kind: str, key: str
    ) -> tuple[AuthAttemptReservation | None, int | None]:
        return self._limiter(kind).begin(key)

    def _limiter(self, kind: str) -> _BoundedRateLimiter:
        if kind == "login":
            return self._login_limiter
        if kind == "extension":
            return self._extension_limiter
        raise ValueError("unknown authentication rate limit")

    @property
    def password_configured(self) -> bool:
        return bool(self.password_hash and self.session_secret)

    @property
    def installer_bearer_configured(self) -> bool:
        return bool(self.token)

    @property
    def enabled(self) -> bool:
        extension_configured = bool(
            self.extension_access_enabled and self.session_secret and self.extension_access_records
        )
        return self.password_configured or self.installer_bearer_configured or extension_configured

    def current_epoch(self) -> int:
        if self.epoch_getter is not None:
            return self.epoch_getter()
        with self._lock:
            return self._epoch

    def revoke_sessions(self) -> int:
        if self.epoch_bumper is not None:
            try:
                return self.epoch_bumper()
            except Exception as error:
                raise DependencyUnavailableError(
                    "authentication revocation state is unavailable"
                ) from error
        with self._lock:
            self._epoch += 1
            return self._epoch

    def mint_session(self, *, ttl_hours: int | None = None) -> str:
        if not self.session_secret:
            raise DependencyUnavailableError("session authentication is not configured")
        if not self._reconcile_ok:
            raise DependencyUnavailableError("authentication password state is unavailable")
        try:
            epoch = self.current_epoch()
        except Exception as error:
            raise DependencyUnavailableError(
                "authentication revocation state is unavailable"
            ) from error
        return auth_core.sign_token(
            self.session_secret,
            epoch=epoch,
            ttl_hours=self.session_ttl_hours if ttl_hours is None else ttl_hours,
            now=self.clock(),
        )

    def verify_session(self, candidate: str | None) -> bool:
        if not candidate or not self.session_secret:
            return False
        if not self._reconcile_ok:
            raise DependencyUnavailableError("authentication password state is unavailable")
        try:
            epoch = self.current_epoch()
        except Exception as error:
            raise DependencyUnavailableError(
                "authentication revocation state is unavailable"
            ) from error
        return auth_core.verify_token(
            candidate,
            self.session_secret,
            current_epoch=epoch,
            now=self.clock(),
        )

    def verify_password(self, candidate: str) -> bool:
        return bool(self.password_hash) and auth_core.verify_password(candidate, self.password_hash)

    def exchange_extension_key(self, candidate: str, *, ttl_hours: int | None = None) -> str:
        if not self.extension_access_enabled:
            raise HTTPException(status_code=403, detail="extension access is disabled")
        if not auth_core.verify_extension_access_key(candidate, self.extension_access_records):
            raise HTTPException(status_code=401, detail="invalid device key")
        return self.mint_session(
            ttl_hours=(self.extension_session_ttl_hours if ttl_hours is None else ttl_hours)
        )

    def _installer_matches(self, candidate: str) -> bool:
        token = self.token
        return token is not None and secrets.compare_digest(candidate.encode(), token.encode())

    def _authorize_candidate(self, candidate: str, *, allow_session: bool = True) -> str:
        if self._installer_matches(candidate):
            return "installer"
        if allow_session and self.verify_session(candidate):
            return "session"
        if "." in candidate:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="session authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="access denied")

    def authorize(self, authorization: str | None) -> None:
        if not self.enabled:
            raise DependencyUnavailableError("API authentication is not configured")
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="bearer authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        self._authorize_candidate(authorization[7:])

    def authenticate_request(self, request: Request, access_control: object | None = None) -> str:
        if _is_trusted_loopback_request(request, access_control):
            return "loopback"
        authorization = request.headers.get("Authorization")
        if authorization:
            if not authorization.lower().startswith("bearer "):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="bearer authentication required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            extension_enabled = bool(
                getattr(access_control, "extension_access_enabled", self.extension_access_enabled)
            )
            return self._authorize_candidate(authorization[7:], allow_session=extension_enabled)
        cookie = request.cookies.get(auth_core.COOKIE_NAME)
        web_enabled = bool(
            getattr(access_control, "web_password_enabled", self.password_configured)
        )
        if web_enabled and self.verify_session(cookie):
            return "cookie"
        if not self.enabled:
            raise DependencyUnavailableError("API authentication is not configured")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _is_trusted_loopback_request(request: Request, access_control: object | None) -> bool:
    if not bool(getattr(access_control, "trust_loopback", False)):
        return False
    peer = request.client.host if request.client else None
    if not auth_core.is_loopback_host(peer):
        return False
    origin_value = request.headers.get("Origin")
    if not origin_value or auth_core.is_extension_origin(origin_value):
        return False
    if request.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
        return False
    effective = auth_core.effective_scheme_host(
        url_scheme=request.url.scheme,
        host_header=request.headers.get("Host"),
        xf_proto=None,
        xf_host=None,
        peer=peer or "",
        trusted_proxies=(),
    )
    if effective is None or not auth_core.is_loopback_host(effective[1]):
        return False
    return auth_core.same_origin(auth_core.parse_origin(origin_value), effective)


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
    async_call = inspect.iscoroutinefunction(callback)
    result = callback() if async_call else await run_sync_port(callback)
    if inspect.isawaitable(result):
        await cast("Awaitable[object]", result)


def build_application_container() -> ApplicationContainer:
    """Compose lazy production adapters without contacting external services."""

    database_settings = DatabaseSettings()
    engine, session_factory = create_engine_and_session(database_settings)

    def uow_factory() -> UnitOfWork:
        return UnitOfWork(session_factory)

    def current_auth_epoch() -> int:
        with uow_factory() as uow:
            return uow.auth_state.current_epoch()

    def bump_auth_epoch() -> int:
        with uow_factory() as uow:
            epoch = uow.auth_state.bump_epoch()
            uow.commit()
            return epoch

    def reconcile_password_fingerprint(fingerprint: str | None) -> bool:
        with uow_factory() as uow:
            changed = uow.auth_state.reconcile_password_fingerprint(fingerprint)
            uow.commit()
            return changed

    access = AccessPolicy.from_environment()
    access.attach_persistence(
        epoch_getter=current_auth_epoch,
        epoch_bumper=bump_auth_epoch,
        fingerprint_reconciler=reconcile_password_fingerprint,
    )

    registry = _DeferredSourceRegistry(lambda: build_default_source_registry(session_factory))

    def validate_source_settings_change(source_id: str, candidate: Mapping[str, object]) -> None:
        build_default_source_registry(
            session_factory,
            settings_overrides={source_id: candidate},
        )

    settings = SettingsService(
        cast("Callable[[], Any]", uow_factory),
        on_change=_apply_runtime_settings,
        deployment_facts=lambda: {
            "installer_bearer_configured": access.installer_bearer_configured,
            "password_configured": access.password_configured,
        },
    )
    source_tasks = SourceTaskService(cast("Callable[[], Any]", uow_factory), registry.get)
    sources = SourceAccountService(
        cast("Callable[[], Any]", uow_factory),
        cipher=_DeferredCredentialCipher(),
        registry=registry.get,
        validate_settings_change=validate_source_settings_change,
    )
    runner, resolver = _build_task_runner(uow_factory, settings)
    profile = ProfileService(
        cast("Callable[[], Any]", uow_factory),
        ai=TaskRunnerProfileDeltaAI(runner) if runner else None,
    )
    feed = FeedService(
        cast("Callable[[], Any]", uow_factory),
        connectors=lambda: registry.get().connectors,
        assessor=TaskRunnerBatchAssessor(runner) if runner else cast("Any", _UnavailableAssessor()),
        query_planner=TaskRunnerKeywordPlanner(runner) if runner else None,
        explainer=TaskRunnerRecommendationExplainer(runner) if runner else None,
        policy=FeedPolicy(),
        settings=settings,
    )
    chat = ChatService(
        cast("Callable[[], Any]", uow_factory),
        responder=(
            TaskRunnerChatResponder(runner) if runner else cast("Any", _UnavailableResponder())
        ),
    )

    def schedule_interval_minutes(job_name: str) -> int:
        schedules = settings.get().schedules
        return {
            "source_sync": schedules.source_sync_interval_minutes,
            "profile_projection": schedules.profile_projection_interval_minutes,
            "feed_replenishment": schedules.feed_replenishment_interval_minutes,
            "cleanup": schedules.cleanup_interval_minutes,
        }[job_name]

    jobs = JobService(
        cast("Callable[[], Any]", uow_factory),
        queue=HueyJobQueue(),
        schedule_interval_minutes=cast("Any", schedule_interval_minutes),
    )
    ai_health, health_client = _build_ai_health()

    def startup() -> None:
        require_schema_at_head(
            database_url=database_settings.url,
            alembic_ini=Path(os.getenv("OPENBILICLAW_ALEMBIC_INI", "alembic.ini")),
        )
        registry.install()
        access.reconcile_password_fingerprint()
        _apply_runtime_settings(settings.get())
        # Running job ownership belongs to the separate worker process. API
        # startup must never mutate a legitimate in-flight worker lease.

    async def shutdown() -> None:
        if resolver is not None:
            await resolver.aclose()
        if health_client is not None:
            await health_client.aclose()
        engine.dispose()

    return ApplicationContainer(
        access=access,
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
    settings: SettingsService,
) -> tuple[TaskRunner | None, LiteLLMModelResolver | None]:
    api_key = os.getenv("OPENBILICLAW_LITELLM_API_KEY")
    if not api_key:
        return None, None
    resolver = LiteLLMModelResolver(
        base_url=os.getenv("OPENBILICLAW_LITELLM_BASE_URL", "http://127.0.0.1:4000"),
        api_key=api_key,
    )
    recorder = TransactionalAIRunRecorder(uow_factory)
    return TaskRunner(model_resolver=resolver, recorder=recorder, settings=settings), resolver


def _apply_runtime_settings(settings: UserSettings) -> None:
    """Apply the existing process-wide network and logging hooks after validation."""

    set_outbound_proxy(settings.network.proxy_url, mode=settings.network.mode)
    apply_owned_handler_levels(
        console_level=settings.logging.console_level,
        file_level=settings.logging.file_level,
    )


def _build_ai_health() -> tuple[Any, AIHealthService | None]:
    api_key = os.getenv("OPENBILICLAW_LITELLM_API_KEY")
    if not api_key:
        return _UnavailableAIHealth(), None
    service = AIHealthService(
        base_url=os.getenv("OPENBILICLAW_LITELLM_BASE_URL", "http://127.0.0.1:4000"),
        api_key=api_key,
        public_admin_url=public_admin_url_from_environment(),
    )
    return service, service


def get_container(request: Request) -> ApplicationContainer:
    return cast("ApplicationContainer", request.app.state.container)


Container = Annotated[ApplicationContainer, Depends(get_container)]


def require_access(
    request: Request,
    container: Container,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(_BEARER_SCHEME),
    ],
    session_cookie: Annotated[str | None, Security(_COOKIE_SCHEME)],
) -> str:
    del credentials, session_cookie  # retained for the generated security schemes
    method = request.method.upper()
    access_control = getattr(container.settings.get(), "access_control", None)
    mechanism = container.access.authenticate_request(request, access_control)
    if mechanism == "cookie" and method in {"POST", "PUT", "PATCH", "DELETE"}:
        _require_cookie_csrf(request)
    return mechanism


def require_cookie_state_change(
    request: Request,
    mechanism: Annotated[str, Depends(require_access)],
) -> None:
    """Apply CSRF verification to a safe-verb route that mutates server state."""

    if mechanism == "cookie":
        _require_cookie_csrf(request)


def require_onboarding_access(
    request: Request,
    container: Container,
) -> None:
    if not container.settings.get().onboarding_complete and not container.access.enabled:
        return
    access_control = getattr(container.settings.get(), "access_control", None)
    mechanism = container.access.authenticate_request(request, access_control)
    if mechanism == "cookie" and request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        _require_cookie_csrf(request)


def _require_cookie_csrf(request: Request) -> None:
    if request.headers.get(auth_core.CSRF_HEADER) is None:
        raise HTTPException(status_code=403, detail="CSRF verification failed")
    origin = auth_core.parse_origin(request.headers.get("Origin"))
    effective = auth_core.effective_scheme_host(
        url_scheme=request.url.scheme,
        host_header=request.headers.get("Host"),
        xf_proto=None,
        xf_host=None,
        peer="",
        trusted_proxies=(),
    )
    if not auth_core.same_origin(origin, effective):
        raise HTTPException(status_code=403, detail="CSRF verification failed")


__all__ = [
    "ACCESS_TOKEN_ENV",
    "AccessPolicy",
    "ApplicationContainer",
    "Container",
    "DependencyUnavailableError",
    "build_application_container",
    "get_container",
    "require_access",
    "require_cookie_state_change",
    "require_onboarding_access",
]
