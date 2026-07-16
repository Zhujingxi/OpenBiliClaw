"""Database-backed typed settings application service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import TracebackType

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

        with self._uow_factory() as uow:
            current = UserSettings.model_validate(uow.settings.get_all())
            candidate = UserSettings.model_validate({**current.model_dump(), **patch})
            uow.settings.replace(candidate.model_dump())
            uow.commit()
        return candidate


class JobScheduler(Protocol):
    def schedule(
        self,
        job_name: str,
        *,
        idempotency_key: str,
        priority: int | None = None,
    ) -> object: ...


class OnboardingService:
    """Apply first-run source selection before scheduling durable bootstrap."""

    def __init__(self, settings: SettingsService, jobs: JobScheduler) -> None:
        self._settings = settings
        self._jobs = jobs

    def status(self) -> UserSettings:
        return self._settings.get()

    def start(self, source_ids: tuple[str, ...]) -> object:
        selected = frozenset(source_ids)
        if selected:
            current = self._settings.get()
            enabled = {source_id: source_id in selected for source_id in current.source_enabled}
            self._settings.update({"source_enabled": enabled})
        source_key = ",".join(sorted(selected)) or "all-enabled"
        return self._jobs.schedule(
            "source_sync",
            idempotency_key=f"onboarding:{source_key}",
        )
