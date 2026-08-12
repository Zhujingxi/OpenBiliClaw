from __future__ import annotations

import asyncio

import pytest

from openbiliclaw.composition.scheduler import AsyncioSchedulerBackend, ScheduledJobsLifecycle
from openbiliclaw.core.jobs import (
    IntervalSchedule,
    JobSpec,
    MissedRunPolicy,
    OverlapPolicy,
)
from openbiliclaw.core.resources import ResourceBudget
from openbiliclaw.core.supervisor import RuntimeSupervisor


@pytest.mark.asyncio
async def test_interval_schedule_fires_through_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    fired = asyncio.Event()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 1:
            await original_sleep(3600)

    async def run() -> None:
        fired.set()

    original_sleep = asyncio.sleep
    monkeypatch.setattr("openbiliclaw.composition.scheduler.asyncio.sleep", fake_sleep)
    supervisor = RuntimeSupervisor({"network": ResourceBudget("network", 1)})
    lifecycle = ScheduledJobsLifecycle(
        supervisor,
        (
            JobSpec(
                "recommendation.discovery",
                IntervalSchedule(5),
                1,
                "network",
                OverlapPolicy.REJECT,
                MissedRunPolicy.SKIP,
                run,
            ),
        ),
    )
    await lifecycle.start()
    for _ in range(100):
        if fired.is_set():
            break
        await original_sleep(0)
    assert fired.is_set()
    for _ in range(100):
        if supervisor.health().jobs and supervisor.health().jobs[0].runs_completed:
            break
        await original_sleep(0)
    assert sleeps[0] == 5
    assert supervisor.health().jobs[0].runs_completed == 1
    await lifecycle.stop()
    assert not await lifecycle.ready()


@pytest.mark.asyncio
async def test_scheduler_backend_validation_and_idempotency() -> None:
    from openbiliclaw.core.jobs import CronSchedule

    backend = AsyncioSchedulerBackend()
    backend.add_job("one", IntervalSchedule(1), lambda _missed: None)
    backend.start()
    backend.start()
    with pytest.raises(RuntimeError, match="after scheduler start"):
        backend.add_job("late", IntervalSchedule(1), lambda _missed: None)
    backend.shutdown()
    with pytest.raises(ValueError, match="cron"):
        backend.add_job("cron", CronSchedule("* * * * *"), lambda _missed: None)
