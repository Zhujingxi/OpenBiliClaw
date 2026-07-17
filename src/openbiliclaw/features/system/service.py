"""Database-backed typed settings application service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, Protocol, Self, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import TracebackType
    from uuid import UUID

from openbiliclaw.features.system.domain import UserSettings

SettingValue = bool | int | float | str | dict[str, bool] | dict[str, float] | None


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

    def __init__(self, uow_factory: Callable[[], SettingsUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def get(self) -> UserSettings:
        """Return validated stored settings overlaid on typed defaults."""

        with self._uow_factory() as uow:
            values = uow.settings.get_all()
        return UserSettings.model_validate(values)

    def update(self, patch: Mapping[str, object]) -> UserSettings:
        """Validate a partial update and persist the full typed settings atomically."""

        if "onboarding_complete" in patch:
            raise ValueError("onboarding completion is workflow-owned")

        with self._uow_factory() as uow:
            current = UserSettings.model_validate(uow.settings.get_all())
            merged_patch = dict(patch)
            for field_name in ("source_enabled", "source_weights"):
                partial = merged_patch.get(field_name)
                if isinstance(partial, dict):
                    merged_patch[field_name] = {
                        **getattr(current, field_name),
                        **partial,
                    }
            candidate = UserSettings.model_validate({**current.model_dump(), **merged_patch})
            uow.settings.replace(candidate.model_dump())
            uow.commit()
        return candidate

    def complete_onboarding(self) -> UserSettings:
        """Monotonically close the first-run access window after feed admission succeeds."""

        with self._uow_factory() as uow:
            current = UserSettings.model_validate(uow.settings.get_all())
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
        enabled = {source_id: source_id in selected for source_id in current.source_enabled}
        self._settings.update({"source_enabled": enabled})
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
