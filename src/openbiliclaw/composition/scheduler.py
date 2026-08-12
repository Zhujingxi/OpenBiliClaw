"""Asyncio scheduler backend and lifecycle owned by Composition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openbiliclaw.core.jobs import CronSchedule, IntervalSchedule, JobScheduler

if TYPE_CHECKING:
    from openbiliclaw.core.jobs import JobSchedule, JobSpec, SchedulerCallback
    from openbiliclaw.core.supervisor import RuntimeSupervisor


@dataclass(slots=True)
class _Scheduled:
    schedule: JobSchedule
    callback: SchedulerCallback


class AsyncioSchedulerBackend:
    """Fixed-interval backend; cron is rejected until a target cron adapter exists."""

    def __init__(self) -> None:
        self._jobs: dict[str, _Scheduled] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._running = False

    def add_job(self, job_id: str, schedule: JobSchedule, callback: SchedulerCallback) -> None:
        if self._running:
            raise RuntimeError("cannot add jobs after scheduler start")
        if isinstance(schedule, CronSchedule):
            raise ValueError("cron schedules require an explicit backend")
        self._jobs[job_id] = _Scheduled(schedule, callback)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for job_id, scheduled in self._jobs.items():
            task = asyncio.create_task(self._run(scheduled), name=f"openbiliclaw:schedule:{job_id}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run(self, scheduled: _Scheduled) -> None:
        if not isinstance(scheduled.schedule, IntervalSchedule):
            raise RuntimeError("unsupported schedule")
        while self._running:
            await asyncio.sleep(scheduled.schedule.seconds)
            if self._running:
                scheduled.callback(0)

    def shutdown(self) -> None:
        self._running = False
        for task in tuple(self._tasks):
            task.cancel()
        self._tasks.clear()


class ScheduledJobsLifecycle:
    """Start supervision before schedules and stop schedules before supervision."""

    def __init__(self, supervisor: RuntimeSupervisor, jobs: tuple[JobSpec, ...]) -> None:
        self._supervisor = supervisor
        self._backend = AsyncioSchedulerBackend()
        self._scheduler = JobScheduler(
            self._backend,
            lambda spec, missed: self._supervisor.trigger(spec, missed_runs=missed),
        )
        for job in jobs:
            self._scheduler.register(job)
        self._running = False

    async def start(self) -> None:
        await self._supervisor.start()
        self._scheduler.start()
        self._running = True

    async def stop(self) -> None:
        self._scheduler.stop()
        await self._supervisor.stop()
        self._running = False

    async def ready(self) -> bool:
        return self._running
