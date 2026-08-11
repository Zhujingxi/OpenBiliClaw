from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from openbiliclaw.core.health import HealthIssue, HealthSnapshot, HealthStatus
from openbiliclaw.core.lifecycle import LifecycleManager, ManagedComponent


@dataclass
class FakeComponent:
    component_id: str
    events: list[str]
    start_gate: asyncio.Event | None = None
    start_entered: asyncio.Event | None = None
    fail_start: bool = False
    fail_stop: bool = False
    stop_gate: asyncio.Event | None = None
    stop_entered: asyncio.Event | None = None
    health_override: HealthStatus | None = None
    started: bool = field(default=False, init=False)

    async def start(self) -> None:
        self.events.append(f"start:{self.component_id}")
        if self.start_entered is not None:
            self.start_entered.set()
        if self.start_gate is not None:
            await self.start_gate.wait()
        if self.fail_start:
            raise RuntimeError("start failed")
        self.started = True

    async def stop(self) -> None:
        self.events.append(f"stop:{self.component_id}")
        self.started = False
        if self.stop_entered is not None:
            self.stop_entered.set()
        if self.stop_gate is not None:
            await self.stop_gate.wait()
        if self.fail_stop:
            raise RuntimeError("stop failed")

    def health(self) -> HealthSnapshot:
        return HealthSnapshot(
            component_id=self.component_id,
            status=self.health_override
            or (HealthStatus.HEALTHY if self.started else HealthStatus.STOPPED),
            checked_at=datetime.now(UTC),
        )


async def test_lifecycle_starts_in_order_and_stops_in_reverse() -> None:
    events: list[str] = []
    first = FakeComponent("first", events)
    second = FakeComponent("second", events)
    manager = LifecycleManager(
        (ManagedComponent(first), ManagedComponent(second)),
    )

    await manager.start()
    await manager.stop()

    assert events == ["start:first", "start:second", "stop:second", "stop:first"]


async def test_partial_start_failure_rolls_back_started_components() -> None:
    events: list[str] = []
    first = FakeComponent("first", events)
    failing = FakeComponent("failing", events, fail_start=True)
    manager = LifecycleManager((ManagedComponent(first), ManagedComponent(failing)))

    with pytest.raises(RuntimeError, match="start failed"):
        await manager.start()

    assert events == ["start:first", "start:failing", "stop:failing", "stop:first"]
    assert not first.started


async def test_cancellation_during_startup_rolls_back_and_propagates() -> None:
    events: list[str] = []
    gate = asyncio.Event()
    entered = asyncio.Event()
    first = FakeComponent("first", events)
    blocked = FakeComponent("blocked", events, start_gate=gate, start_entered=entered)
    manager = LifecycleManager((ManagedComponent(first), ManagedComponent(blocked)))
    startup = asyncio.create_task(manager.start())
    await entered.wait()

    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup

    assert events[-1] == "stop:first"
    assert not first.started


async def test_optional_component_failure_is_reported_as_degraded() -> None:
    events: list[str] = []
    required = FakeComponent("required", events)
    optional = FakeComponent("optional", events, fail_start=True)
    manager = LifecycleManager(
        (ManagedComponent(required), ManagedComponent(optional, optional=True))
    )

    await manager.start()

    assert manager.health().status is HealthStatus.DEGRADED
    await manager.stop()


async def test_failed_replacement_keeps_old_graph_active() -> None:
    events: list[str] = []
    old = FakeComponent("old", events)
    failing = FakeComponent("new", events, fail_start=True)
    manager = LifecycleManager((ManagedComponent(old),))
    await manager.start()

    replaced = await manager.replace((ManagedComponent(failing),))

    assert not replaced
    assert manager.components == (old,)
    assert old.started
    await manager.stop()


async def test_stop_continues_after_component_failure() -> None:
    events: list[str] = []
    first = FakeComponent("first", events)
    failing = FakeComponent("failing", events, fail_stop=True)
    manager = LifecycleManager((ManagedComponent(first), ManagedComponent(failing)))
    await manager.start()

    with pytest.raises(ExceptionGroup, match="shutdown failed"):
        await manager.stop()

    assert events[-2:] == ["stop:failing", "stop:first"]
    assert manager.health().status is HealthStatus.STOPPED


async def test_health_propagates_required_component_failure() -> None:
    events: list[str] = []
    unhealthy = FakeComponent("unhealthy", events, health_override=HealthStatus.UNHEALTHY)
    manager = LifecycleManager((ManagedComponent(unhealthy),))
    await manager.start()

    assert manager.health().status is HealthStatus.UNHEALTHY
    with pytest.raises(ValueError, match="positive"):
        await manager.replace((), drain_timeout_seconds=0)
    await manager.stop()


async def test_replacement_drain_timeout_is_reported_as_degraded_health() -> None:
    events: list[str] = []
    stop_entered = asyncio.Event()
    old = FakeComponent(
        "old",
        events,
        stop_gate=asyncio.Event(),
        stop_entered=stop_entered,
    )
    new = FakeComponent("new", events)
    manager = LifecycleManager((ManagedComponent(old),))
    await manager.start()

    replaced = await manager.replace((ManagedComponent(new),), drain_timeout_seconds=1e-9)

    assert replaced
    assert stop_entered.is_set()
    assert manager.health().status is HealthStatus.DEGRADED
    assert manager.health().issue is HealthIssue.SHUTDOWN_TIMEOUT
    await manager.stop()


async def test_replacement_drain_failure_is_reported_as_degraded_health() -> None:
    events: list[str] = []
    old = FakeComponent("old", events, fail_stop=True)
    new = FakeComponent("new", events)
    manager = LifecycleManager((ManagedComponent(old),))
    await manager.start()

    assert await manager.replace((ManagedComponent(new),))
    assert manager.health().status is HealthStatus.DEGRADED
    assert manager.health().issue is HealthIssue.SHUTDOWN_TIMEOUT
    await manager.stop()


async def test_successful_replacement_starts_new_before_draining_old() -> None:
    events: list[str] = []
    old = FakeComponent("old", events)
    new = FakeComponent("new", events)
    manager = LifecycleManager((ManagedComponent(old),))
    await manager.start()

    replaced = await manager.replace((ManagedComponent(new),))

    assert replaced
    assert manager.components == (new,)
    assert events == ["start:old", "start:new", "stop:old"]
    await manager.stop()
