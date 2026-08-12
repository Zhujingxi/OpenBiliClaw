"""Composition-owned staged lifecycle with readiness and rollback."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from typing import Protocol

from openbiliclaw.core.health import HealthIssue, HealthSnapshot, HealthStatus


class ComponentStage(IntEnum):
    """Required production startup order."""

    INFRASTRUCTURE = 1
    SERVICE = 2
    CORE_JOBS = 3
    HOST = 4


class Component(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def ready(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class RuntimeComponent:
    component_id: str
    stage: ComponentStage
    component: Component
    optional: bool = False

    def __post_init__(self) -> None:
        if not self.component_id:
            raise ValueError("component_id must not be empty")


class LifecyclePlan:
    """Start by stage, verify readiness, and stop in exact reverse order."""

    def __init__(self, components: tuple[RuntimeComponent, ...]) -> None:
        self._configured = tuple(sorted(components, key=lambda item: item.stage))
        self._active: list[RuntimeComponent] = []
        self._degraded: list[str] = []

    @property
    def active_component_ids(self) -> tuple[str, ...]:
        return tuple(item.component_id for item in self._active)

    async def start(self) -> None:
        if self._active:
            return
        for item in self._configured:
            try:
                await item.component.start()
                if not await item.component.ready():
                    raise RuntimeError(f"component is not ready: {item.component_id}")
            except BaseException:
                # A failing stop must not mask the start failure or skip rollback.
                with suppress(Exception):
                    await item.component.stop()
                if item.optional:
                    self._degraded.append(item.component_id)
                    continue
                await self._rollback()
                raise
            self._active.append(item)

    async def _rollback(self) -> None:
        for item in reversed(self._active):
            with suppress(Exception):
                await item.component.stop()
        self._active.clear()
        self._degraded.clear()

    async def stop(self) -> None:
        failures: list[Exception] = []
        for item in reversed(self._active):
            try:
                await item.component.stop()
            except Exception as error:
                failures.append(error)
        self._active.clear()
        self._degraded.clear()
        if failures:
            raise ExceptionGroup("runtime shutdown failed", failures)

    async def ready(self) -> bool:
        return bool(self._active)

    def health(self) -> HealthSnapshot:
        if not self._active:
            status, issue = HealthStatus.STOPPED, None
        elif self._degraded:
            status, issue = HealthStatus.DEGRADED, HealthIssue.OPTIONAL_COMPONENT_FAILURE
        else:
            status, issue = HealthStatus.HEALTHY, None
        return HealthSnapshot(
            component_id="composition.lifecycle",
            status=status,
            checked_at=datetime.now(UTC),
            issue=issue,
        )
