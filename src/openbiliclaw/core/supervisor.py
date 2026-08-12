"""Single-owner task supervision with budgets, timeouts, and bounded health."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from openbiliclaw.core.health import (
    HealthIssue,
    HealthSnapshot,
    HealthStatus,
    JobHealth,
    JobResult,
)
from openbiliclaw.core.jobs import JobDecision, JobSpec

if TYPE_CHECKING:
    from openbiliclaw.core.resources import ResourceBudget

TimeoutFactory = Callable[[float], AbstractAsyncContextManager[object]]


def _stdlib_timeout(seconds: float) -> AbstractAsyncContextManager[object]:
    return asyncio.timeout(seconds)


@dataclass(slots=True)
class _MutableJobHealth:
    active_runs: int = 0
    last_result: JobResult | None = None
    runs_started: int = 0
    runs_completed: int = 0


class RuntimeSupervisor:
    """Own every application-created task and report payload-free outcomes."""

    def __init__(
        self,
        resources: Mapping[str, ResourceBudget],
        *,
        timeout_factory: TimeoutFactory = _stdlib_timeout,
        shutdown_grace_seconds: float = 30.0,
    ) -> None:
        if shutdown_grace_seconds <= 0:
            raise ValueError("shutdown_grace_seconds must be positive")
        self._resources = dict(resources)
        self._timeout_factory = timeout_factory
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._task_group: asyncio.TaskGroup | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._jobs: dict[str, _MutableJobHealth] = {}
        self._started = False
        self._paused = False

    async def start(self) -> None:
        """Open the owned task group before accepting work."""
        if self._started:
            return
        task_group = asyncio.TaskGroup()
        await task_group.__aenter__()
        self._task_group = task_group
        self._started = True

    def trigger(self, spec: JobSpec, *, missed_runs: int = 0) -> JobDecision:
        """Apply policy and admit one run into the owned task group."""
        if not self._started or self._task_group is None:
            raise RuntimeError("runtime supervisor is not started")
        if self._paused:
            return JobDecision.REJECT_PAUSED
        resource = self._resources.get(spec.resource)
        if resource is None:
            raise KeyError(f"unknown resource budget: {spec.resource}")
        state = self._jobs.setdefault(spec.job_id, _MutableJobHealth())
        decision = spec.decide(active_runs=state.active_runs, missed_runs=missed_runs)
        if decision is not JobDecision.RUN:
            return decision

        state.active_runs += 1
        state.runs_started += 1
        task = self._task_group.create_task(
            self._run(spec, resource, state), name=f"openbiliclaw:{spec.job_id}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return decision

    def pause(self) -> None:
        """Reject new work while allowing admitted runs to drain."""
        self._paused = True

    def resume(self) -> None:
        """Resume admission after a pause."""
        self._paused = False

    async def _run(
        self,
        spec: JobSpec,
        resource: ResourceBudget,
        state: _MutableJobHealth,
    ) -> None:
        try:
            async with self._timeout_factory(spec.timeout_seconds), resource.acquire():
                await spec.run()
        except TimeoutError:
            state.last_result = JobResult.TIMEOUT
        except asyncio.CancelledError:
            state.last_result = JobResult.CANCELLED
            raise
        except Exception:
            state.last_result = JobResult.ERROR
        else:
            state.last_result = JobResult.SUCCESS
        finally:
            state.active_runs -= 1
            state.runs_completed += 1

    async def wait_idle(self) -> None:
        """Wait until all work admitted at each observation point has finished."""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def drain(self, timeout_seconds: float) -> bool:
        """Wait up to a deadline without cancelling unfinished work."""
        if timeout_seconds < 0:
            raise ValueError("drain timeout must not be negative")
        if not self._tasks:
            return True
        _, pending = await asyncio.wait(tuple(self._tasks), timeout=timeout_seconds)
        return not pending

    async def stop(self) -> None:
        """Cancel owned work, wait for cleanup, and close the task group."""
        task_group = self._task_group
        if task_group is None:
            return
        self._started = False
        self._paused = False
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        try:
            async with asyncio.timeout(self._shutdown_grace_seconds):
                if tasks:
                    await asyncio.wait(tasks)
                await task_group.__aexit__(None, None, None)
        except TimeoutError:
            # Detach pathological cancellation-resistant tasks after the grace
            # deadline; shutdown must not retain an open TaskGroup reference.
            asyncio.create_task(task_group.__aexit__(None, None, None))
        finally:
            self._task_group = None
            self._tasks.clear()

    def health(self) -> HealthSnapshot:
        """Return immutable job counters without exception text or payloads."""
        jobs = tuple(
            JobHealth(
                job_id=job_id,
                active_runs=state.active_runs,
                last_result=state.last_result,
                runs_started=state.runs_started,
                runs_completed=state.runs_completed,
            )
            for job_id, state in sorted(self._jobs.items())
        )
        has_errors = any(job.last_result is JobResult.ERROR for job in jobs)
        has_timeouts = any(job.last_result is JobResult.TIMEOUT for job in jobs)
        if not self._started:
            status = HealthStatus.STOPPED
            issue = None
        elif has_errors:
            status = HealthStatus.DEGRADED
            issue = HealthIssue.JOB_FAILURE
        elif has_timeouts:
            status = HealthStatus.DEGRADED
            issue = HealthIssue.JOB_TIMEOUT
        else:
            status = HealthStatus.HEALTHY
            issue = None
        return HealthSnapshot(
            component_id="runtime.supervisor",
            status=status,
            checked_at=datetime.now(UTC),
            issue=issue,
            jobs=jobs,
        )
