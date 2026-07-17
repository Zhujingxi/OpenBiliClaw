"""Database-backed typed settings application service."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Literal, Protocol, Self, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType
    from uuid import UUID

from openbiliclaw.features.system.domain import UserSettings

SettingValue = bool | int | float | str | dict[str, Any] | None

_READ_ONLY_PATHS = frozenset(
    {
        "onboarding_complete",
        "access_control.installer_bearer_configured",
        "access_control.password_configured",
        "jobs.worker_concurrency",
        "logging.directory",
    }
)


def _deep_merge(current: Mapping[str, object], patch: Mapping[str, object]) -> dict[str, object]:
    merged = dict(current)
    for key, value in patch.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _contains_path(values: Mapping[str, object], path: str) -> bool:
    current: object = values
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return False
        current = current[segment]
    return True


def _deployment_overlay(
    settings: UserSettings, facts: Mapping[str, object] | None = None
) -> UserSettings:
    """Refresh deployment facts without exposing the underlying secret values."""

    try:
        worker_concurrency = int(os.getenv("OPENBILICLAW_WORKERS", "4"))
    except ValueError:
        worker_concurrency = 4
    worker_concurrency = max(1, min(4, worker_concurrency))
    access_control = settings.access_control.model_copy(
        update={
            "installer_bearer_configured": bool(
                (facts or {}).get(
                    "installer_bearer_configured", os.getenv("OPENBILICLAW_ACCESS_TOKEN")
                )
            ),
            "password_configured": bool(
                (facts or {}).get(
                    "password_configured", os.getenv("OPENBILICLAW_WEB_PASSWORD_HASH")
                )
            ),
        }
    )
    jobs = settings.jobs.model_copy(update={"worker_concurrency": worker_concurrency})
    return settings.model_copy(update={"access_control": access_control, "jobs": jobs})


class SettingsRepository(Protocol):
    """Port required by the settings service."""

    def get_all(self) -> dict[str, SettingValue]: ...

    def replace(self, values: Mapping[str, SettingValue]) -> None: ...


class SettingsUnitOfWork(Protocol):
    """Minimal transaction port required by settings operations."""

    settings: SettingsRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class SettingsService:
    """Validate the complete setting set before atomically replacing stored values."""

    def __init__(
        self,
        uow_factory: Callable[[], SettingsUnitOfWork],
        *,
        on_change: Callable[[UserSettings], None] | None = None,
        deployment_facts: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._on_change = on_change
        self._deployment_facts = deployment_facts

    def _overlay(self, settings: UserSettings) -> UserSettings:
        facts = self._deployment_facts() if self._deployment_facts is not None else None
        return _deployment_overlay(settings, facts)

    def get(self) -> UserSettings:
        """Return validated stored settings overlaid on typed defaults."""

        with self._uow_factory() as uow:
            values = uow.settings.get_all()
        return self._overlay(UserSettings.model_validate(values))

    def update(self, patch: Mapping[str, object]) -> UserSettings:
        """Validate a partial update and persist the full typed settings atomically."""

        if "onboarding_complete" in patch:
            raise ValueError("onboarding completion is workflow-owned and read-only")
        for path in _READ_ONLY_PATHS - {"onboarding_complete"}:
            if _contains_path(patch, path):
                raise ValueError(f"{path} is deployment-owned and read-only")

        with self._uow_factory() as uow:
            current = self._overlay(UserSettings.model_validate(uow.settings.get_all()))
            candidate = self._overlay(
                UserSettings.model_validate(_deep_merge(current.model_dump(), patch))
            )
            uow.settings.replace(candidate.model_dump())
            uow.commit()
        if self._on_change is not None:
            self._on_change(candidate)
        return candidate

    def complete_onboarding(self) -> UserSettings:
        """Monotonically close the first-run access window after feed admission succeeds."""

        with self._uow_factory() as uow:
            current = self._overlay(UserSettings.model_validate(uow.settings.get_all()))
            if current.onboarding_complete:
                return current
            completed = current.model_copy(update={"onboarding_complete": True})
            uow.settings.replace(completed.model_dump())
            uow.commit()
        return completed


class JobRun(Protocol):
    """Application-facing projection of a durable background run."""

    @property
    def job_name(self) -> str: ...

    @property
    def idempotency_key(self) -> str: ...

    @property
    def status(self) -> object: ...

    @property
    def id(self) -> UUID: ...


JobRunCo = TypeVar("JobRunCo", bound=JobRun, covariant=True)
JobRunT = TypeVar("JobRunT", bound=JobRun)


class JobScheduler(Protocol[JobRunCo]):
    def schedule(
        self,
        job_name: str,
        *,
        idempotency_key: str,
        priority: int | None = None,
    ) -> JobRunCo: ...

    def register_success_callback(self, callback: Callable[[JobRunCo], None]) -> None: ...

    def restart_terminal(self, run_id: UUID) -> JobRunCo: ...

    def inspect(self, run_id: UUID) -> JobRunCo: ...

    def find_by_idempotency_key(self, idempotency_key: str) -> JobRunCo | None: ...


OnboardingStage = Literal["source_sync", "profile_projection", "feed_replenishment"]


@dataclass(frozen=True, slots=True)
class OnboardingWorkflowProgress(Generic[JobRunT]):
    """Persisted current-stage projection for one onboarding root run."""

    root_run_id: UUID
    stage: OnboardingStage
    run: JobRunT
    onboarding_complete: bool


class OnboardingService(Generic[JobRunT]):
    """Own the durable source -> profile -> feed retained journey."""

    _NEXT_STAGE = {
        "source_sync": "profile_projection",
        "profile_projection": "feed_replenishment",
    }

    def __init__(self, settings: SettingsService, jobs: JobScheduler[JobRunT]) -> None:
        self._settings = settings
        self._jobs = jobs
        jobs.register_success_callback(self._on_success)

    def status(self) -> UserSettings:
        return self._settings.get()

    def start(self, source_ids: tuple[str, ...]) -> JobRunT:
        selected = frozenset(source_ids)
        if not selected:
            raise ValueError("onboarding requires at least one source")
        current = self._settings.get()
        enabled = {source_id: source_id in selected for source_id in current.sources.enabled}
        self._settings.update({"sources": {"enabled": enabled}})
        source_key = ",".join(sorted(selected))
        run = self._jobs.schedule(
            "source_sync",
            idempotency_key=f"onboarding:{source_key}",
        )
        run = self._resume_terminal(run)
        # Explicit restart walks an existing successful prefix and resumes its stopped stage.
        self._advance(run, resume_terminal=True)
        return run

    def progress(self, root_run_id: UUID) -> OnboardingWorkflowProgress[JobRunT]:
        """Resolve the current durable child without creating continuation rows."""

        root = self._jobs.inspect(root_run_id)
        root_prefix = "source_sync:onboarding:"
        if root.job_name != "source_sync" or not root.idempotency_key.startswith(root_prefix):
            raise LookupError("job run is not an onboarding root")

        workflow_key = root.idempotency_key.removeprefix("source_sync:")
        stage: OnboardingStage = "source_sync"
        current = root
        if str(root.status) == "succeeded":
            for candidate_stage in ("profile_projection", "feed_replenishment"):
                child = self._jobs.find_by_idempotency_key(f"{candidate_stage}:{workflow_key}")
                if child is None:
                    break
                stage = candidate_stage
                current = child
                if str(child.status) != "succeeded":
                    break
        return OnboardingWorkflowProgress(
            root_run_id=root.id,
            stage=stage,
            run=current,
            onboarding_complete=self._settings.get().onboarding_complete,
        )

    def _on_success(self, run: JobRunT) -> None:
        self._advance(run, resume_terminal=False)

    def _resume_terminal(self, run: JobRunT) -> JobRunT:
        if str(run.status) in {"failed", "cancelled"}:
            return self._jobs.restart_terminal(run.id)
        return run

    def _advance(self, run: JobRunT, *, resume_terminal: bool) -> None:
        if str(run.status) != "succeeded":
            return
        prefix = f"{run.job_name}:onboarding:"
        if not run.idempotency_key.startswith(prefix):
            return
        workflow_key = run.idempotency_key.removeprefix(f"{run.job_name}:")
        next_stage = self._NEXT_STAGE.get(run.job_name)
        if next_stage is not None:
            next_run = self._jobs.schedule(next_stage, idempotency_key=workflow_key)
            if resume_terminal:
                next_run = self._resume_terminal(next_run)
                self._advance(next_run, resume_terminal=True)
            return
        if run.job_name == "feed_replenishment":
            self._settings.complete_onboarding()
