"""Ordered component lifecycle and replacement-based reload."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable

from openbiliclaw.core.health import HealthIssue, HealthSnapshot, HealthStatus


class LifecycleComponent(Protocol):
    """Minimal contract implemented by composition-owned components."""

    @property
    def component_id(self) -> str: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def health(self) -> HealthSnapshot: ...


@dataclass(frozen=True, slots=True)
class ManagedComponent:
    """A lifecycle component with an explicit degraded-start policy."""

    component: LifecycleComponent
    optional: bool = False


class LifecycleManager:
    """Start in order, stop in reverse, and atomically publish replacements."""

    def __init__(self, components: Iterable[ManagedComponent]) -> None:
        self._configured = tuple(components)
        self._active: tuple[ManagedComponent, ...] = ()
        self._optional_failures: set[str] = set()
        self._drain_issue: HealthIssue | None = None
        self._started = False

    @property
    def components(self) -> tuple[LifecycleComponent, ...]:
        return tuple(managed.component for managed in self._active)

    async def start(self) -> None:
        """Start required components and roll back all partial starts on failure."""
        if self._started:
            return
        active: list[ManagedComponent] = []
        optional_failures: set[str] = set()
        try:
            for managed in self._configured:
                try:
                    await managed.component.start()
                except asyncio.CancelledError:
                    await self._cleanup_failed_start(managed, active)
                    raise
                except Exception:
                    await self._safe_stop(managed.component)
                    if managed.optional:
                        optional_failures.add(managed.component.component_id)
                        continue
                    for started in reversed(active):
                        await self._safe_stop(started.component)
                    raise
                active.append(managed)
        except BaseException:
            self._active = ()
            self._started = False
            raise
        self._active = tuple(active)
        self._optional_failures = optional_failures
        self._drain_issue = None
        self._started = True

    async def _cleanup_failed_start(
        self, failed: ManagedComponent, active: list[ManagedComponent]
    ) -> None:
        await self._safe_stop(failed.component)
        for managed in reversed(active):
            await self._safe_stop(managed.component)

    async def _safe_stop(self, component: LifecycleComponent) -> None:
        with suppress(Exception):
            await component.stop()

    async def _stop_reverse(self, components: Iterable[ManagedComponent]) -> None:
        failures: list[Exception] = []
        for managed in reversed(tuple(components)):
            try:
                await managed.component.stop()
            except Exception as error:
                failures.append(error)
        if failures:
            raise ExceptionGroup("component shutdown failed", failures)

    async def stop(self) -> None:
        """Stop all active components in reverse startup order."""
        active = self._active
        self._active = ()
        self._started = False
        self._optional_failures.clear()
        self._drain_issue = None
        await self._stop_reverse(active)

    async def replace(
        self,
        components: Iterable[ManagedComponent],
        *,
        drain_timeout_seconds: float = 30.0,
    ) -> bool:
        """Ready a new graph, publish it, then drain the old graph."""
        if drain_timeout_seconds <= 0:
            raise ValueError("drain timeout must be positive")
        replacement = LifecycleManager(components)
        try:
            await replacement.start()
        except Exception:
            return False

        old = self._active
        self._configured = replacement._configured
        self._active = replacement._active
        self._optional_failures = replacement._optional_failures
        self._drain_issue = None
        self._started = True
        try:
            async with asyncio.timeout(drain_timeout_seconds):
                await self._stop_reverse(old)
        except (TimeoutError, ExceptionGroup):
            self._drain_issue = HealthIssue.SHUTDOWN_TIMEOUT
        return True

    def health(self) -> HealthSnapshot:
        """Aggregate component states without copying component diagnostics."""
        if not self._started:
            status = HealthStatus.STOPPED
            issue = None
        else:
            component_states = tuple(managed.component.health().status for managed in self._active)
            if any(state is HealthStatus.UNHEALTHY for state in component_states):
                status = HealthStatus.UNHEALTHY
                issue = HealthIssue.COMPONENT_FAILURE
            elif self._drain_issue is not None:
                status = HealthStatus.DEGRADED
                issue = self._drain_issue
            elif self._optional_failures or any(
                state is HealthStatus.DEGRADED for state in component_states
            ):
                status = HealthStatus.DEGRADED
                issue = HealthIssue.OPTIONAL_COMPONENT_FAILURE
            else:
                status = HealthStatus.HEALTHY
                issue = None
        return HealthSnapshot(
            component_id="runtime.lifecycle",
            status=status,
            checked_at=datetime.now(UTC),
            issue=issue,
        )
