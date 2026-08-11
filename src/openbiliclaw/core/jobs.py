"""Typed schedules and deterministic background-job admission policies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class OverlapPolicy(StrEnum):
    """Policy applied when the same job is already active."""

    REJECT = "reject"
    ALLOW = "allow"


class MissedRunPolicy(StrEnum):
    """Policy applied to scheduler triggers known to be late."""

    SKIP = "skip"
    RUN_ONCE = "run_once"


class JobDecision(StrEnum):
    """Deterministic result of applying admission policy."""

    RUN = "run"
    REJECT_OVERLAP = "reject_overlap"
    REJECT_PAUSED = "reject_paused"
    SKIP_MISSED = "skip_missed"


@dataclass(frozen=True, slots=True)
class IntervalSchedule:
    """Fixed-delay scheduler metadata."""

    seconds: float

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise ValueError("interval seconds must be positive")


@dataclass(frozen=True, slots=True)
class CronSchedule:
    """Five-field cron metadata consumed by the host scheduler adapter."""

    expression: str

    def __post_init__(self) -> None:
        if len(self.expression.split()) != 5:
            raise ValueError("cron expression must contain five fields")


JobSchedule = IntervalSchedule | CronSchedule
JobCallable = Callable[[], Awaitable[None]]
SchedulerCallback = Callable[[int], None]
JobTrigger = Callable[["JobSpec", int], JobDecision]


class SchedulerBackend(Protocol):
    """Typed boundary implemented by the host's existing scheduler adapter."""

    def add_job(self, job_id: str, schedule: JobSchedule, callback: SchedulerCallback) -> None: ...

    def start(self) -> None: ...

    def shutdown(self) -> None: ...


@dataclass(frozen=True, slots=True)
class JobSpec:
    """Complete execution policy for one proactive unit of work."""

    job_id: str
    schedule: JobSchedule
    timeout_seconds: float
    resource: str
    overlap_policy: OverlapPolicy
    missed_run_policy: MissedRunPolicy
    run: JobCallable

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not self.resource:
            raise ValueError("resource must not be empty")

    def decide(self, *, active_runs: int, missed_runs: int) -> JobDecision:
        """Apply overlap and missed-run policy without side effects."""
        if active_runs < 0 or missed_runs < 0:
            raise ValueError("run counts must not be negative")
        if active_runs and self.overlap_policy is OverlapPolicy.REJECT:
            return JobDecision.REJECT_OVERLAP
        if missed_runs and self.missed_run_policy is MissedRunPolicy.SKIP:
            return JobDecision.SKIP_MISSED
        return JobDecision.RUN


class JobScheduler:
    """Bind scheduler ticks to job admission without embedding product logic."""

    def __init__(self, backend: SchedulerBackend, trigger: JobTrigger) -> None:
        self._backend = backend
        self._trigger = trigger
        self._jobs: dict[str, JobSpec] = {}
        self._started = False

    def register(self, spec: JobSpec) -> None:
        if self._started:
            raise RuntimeError("cannot register jobs after scheduler start")
        if spec.job_id in self._jobs:
            raise ValueError(f"duplicate job_id: {spec.job_id}")
        self._jobs[spec.job_id] = spec

    def _callback(self, spec: JobSpec) -> SchedulerCallback:
        def admit(missed_runs: int) -> None:
            self._trigger(spec, missed_runs)

        return admit

    def start(self) -> None:
        if self._started:
            return
        for spec in self._jobs.values():
            self._backend.add_job(spec.job_id, spec.schedule, self._callback(spec))
        self._backend.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._backend.shutdown()
        self._started = False
