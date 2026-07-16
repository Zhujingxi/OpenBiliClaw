"""Database-backed typed settings application service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import TracebackType

from openbiliclaw.features.system.domain import UserSettings

SettingValue = bool | int | float | str | None


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
