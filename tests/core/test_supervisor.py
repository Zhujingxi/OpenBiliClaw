from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.core.health import HealthIssue, JobResult
from openbiliclaw.core.jobs import (
    IntervalSchedule,
    JobDecision,
    JobSpec,
    MissedRunPolicy,
    OverlapPolicy,
)
from openbiliclaw.core.resources import ResourceBudget
from openbiliclaw.core.supervisor import RuntimeSupervisor

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


def _spec(
    run: Callable[[], Awaitable[None]],
    *,
    job_id: str = "refresh",
    timeout_seconds: float = 10,
    overlap: OverlapPolicy = OverlapPolicy.REJECT,
    missed: MissedRunPolicy = MissedRunPolicy.SKIP,
) -> JobSpec:
    return JobSpec(
        job_id=job_id,
        schedule=IntervalSchedule(seconds=60),
        timeout_seconds=timeout_seconds,
        resource="default",
        overlap_policy=overlap,
        missed_run_policy=missed,
        run=run,
    )


async def test_supervisor_rejects_overlap_and_records_success() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def work() -> None:
        entered.set()
        await release.wait()

    supervisor = RuntimeSupervisor({"default": ResourceBudget("default", 1)})
    await supervisor.start()
    spec = _spec(work)

    assert supervisor.trigger(spec) is JobDecision.RUN
    await entered.wait()
    assert supervisor.trigger(spec) is JobDecision.REJECT_OVERLAP
    release.set()
    await supervisor.wait_idle()

    job = supervisor.health().jobs[0]
    assert job.runs_started == 1
    assert job.runs_completed == 1
    assert job.last_result is JobResult.SUCCESS
    await supervisor.stop()


async def test_pause_rejects_new_admission_until_resumed() -> None:
    called = False

    async def work() -> None:
        nonlocal called
        called = True

    supervisor = RuntimeSupervisor({"default": ResourceBudget("default", 1)})
    await supervisor.start()
    supervisor.pause()

    assert supervisor.trigger(_spec(work)) is JobDecision.REJECT_PAUSED
    assert not called
    supervisor.resume()
    assert supervisor.trigger(_spec(work)) is JobDecision.RUN
    await supervisor.wait_idle()
    assert called
    await supervisor.stop()


async def test_supervisor_skips_missed_run_by_policy() -> None:
    called = False

    async def work() -> None:
        nonlocal called
        called = True

    supervisor = RuntimeSupervisor({"default": ResourceBudget("default", 1)})
    await supervisor.start()

    decision = supervisor.trigger(_spec(work), missed_runs=2)

    assert decision is JobDecision.SKIP_MISSED
    assert not called
    await supervisor.stop()


async def test_supervisor_reports_timeout_using_injected_fake_clock() -> None:
    called = False

    @asynccontextmanager
    async def fake_timeout(_: float) -> AsyncIterator[None]:
        nonlocal called
        called = True
        raise TimeoutError
        yield

    async def work() -> None:
        raise AssertionError("fake timeout must fire before work")

    supervisor = RuntimeSupervisor(
        {"default": ResourceBudget("default", 1)}, timeout_factory=fake_timeout
    )
    await supervisor.start()
    assert supervisor.trigger(_spec(work)) is JobDecision.RUN
    await supervisor.wait_idle()

    assert called
    health = supervisor.health()
    assert health.jobs[0].last_result is JobResult.TIMEOUT
    assert health.issue is HealthIssue.JOB_TIMEOUT
    await supervisor.stop()
    assert supervisor.health().issue is None


async def test_stdlib_timeout_cancels_running_work_without_leaking_cancellation() -> None:
    entered = asyncio.Event()

    async def work() -> None:
        entered.set()
        await asyncio.Event().wait()

    supervisor = RuntimeSupervisor({"default": ResourceBudget("default", 1)})
    await supervisor.start()
    supervisor.trigger(_spec(work, timeout_seconds=1e-9))
    await supervisor.wait_idle()

    assert entered.is_set()
    assert supervisor.health().jobs[0].last_result is JobResult.TIMEOUT
    await supervisor.stop()


async def test_shutdown_deadline_bounds_task_suppressing_cancellation() -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def stubborn_work() -> None:
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()

    supervisor = RuntimeSupervisor(
        {"default": ResourceBudget("default", 1)}, shutdown_grace_seconds=0.01
    )
    await supervisor.start()
    supervisor.trigger(_spec(stubborn_work))
    await entered.wait()

    await supervisor.stop()

    assert cancelled.is_set()
    release.set()
    await asyncio.sleep(0)
    await supervisor.stop()


async def test_shutdown_grace_must_be_positive() -> None:
    with pytest.raises(ValueError, match="shutdown_grace_seconds"):
        RuntimeSupervisor({"default": ResourceBudget("default", 1)}, shutdown_grace_seconds=0)


async def test_shutdown_cancels_work_and_leaves_no_owned_task() -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def work() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    supervisor = RuntimeSupervisor({"default": ResourceBudget("default", 1)})
    await supervisor.start()
    supervisor.trigger(_spec(work))
    await entered.wait()

    await supervisor.stop()

    assert cancelled.is_set()
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and task.get_name().startswith("openbiliclaw:")
    ]


async def test_cancellation_is_not_recorded_as_normal_failure() -> None:
    entered = asyncio.Event()

    async def work() -> None:
        entered.set()
        await asyncio.Event().wait()

    supervisor = RuntimeSupervisor({"default": ResourceBudget("default", 1)})
    await supervisor.start()
    supervisor.trigger(_spec(work))
    await entered.wait()
    await supervisor.stop()

    assert supervisor.health().jobs[0].last_result is JobResult.CANCELLED


async def test_drain_and_failure_paths_are_bounded() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked() -> None:
        entered.set()
        await release.wait()

    async def failing() -> None:
        raise RuntimeError("credential=must-not-enter-health")

    supervisor = RuntimeSupervisor({"default": ResourceBudget("default", 2)})
    with pytest.raises(RuntimeError, match="not started"):
        supervisor.trigger(_spec(blocked))
    await supervisor.start()
    await supervisor.start()
    assert await supervisor.drain(0)
    supervisor.trigger(_spec(blocked, job_id="blocked"))
    supervisor.trigger(_spec(failing, job_id="failing"))
    await entered.wait()
    assert not await supervisor.drain(0)
    with pytest.raises(ValueError, match="must not be negative"):
        await supervisor.drain(-1)
    release.set()
    await supervisor.wait_idle()

    health = supervisor.health()
    assert health.jobs[1].last_result is JobResult.ERROR
    assert "must-not-enter-health" not in health.model_dump_json()
    await supervisor.stop()
    await supervisor.stop()


async def test_unknown_resource_is_rejected_before_task_creation() -> None:
    async def work() -> None:
        return None

    supervisor = RuntimeSupervisor({"default": ResourceBudget("default", 1)})
    await supervisor.start()
    spec = _spec(work)
    missing_resource_spec = JobSpec(
        job_id=spec.job_id,
        schedule=spec.schedule,
        timeout_seconds=spec.timeout_seconds,
        resource="missing",
        overlap_policy=spec.overlap_policy,
        missed_run_policy=spec.missed_run_policy,
        run=spec.run,
    )

    with pytest.raises(KeyError, match="missing"):
        supervisor.trigger(missing_resource_spec)
    await supervisor.stop()
