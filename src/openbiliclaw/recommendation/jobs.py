"""Core JobSpecs for proactive recommendation work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from openbiliclaw.core.jobs import IntervalSchedule, JobSpec, MissedRunPolicy, OverlapPolicy

JobRun = Callable[[], Awaitable[None]]


def recommendation_jobs(
    *, discovery: JobRun, evaluation: JobRun, expiry: JobRun, replenishment: JobRun
) -> tuple[JobSpec, ...]:
    return (
        _job("recommendation.discovery", 300, "network", discovery),
        _job("recommendation.evaluation", 60, "model", evaluation),
        _job("recommendation.expiry", 900, "database", expiry),
        _job("recommendation.replenishment", 120, "network", replenishment),
    )


def _job(job_id: str, seconds: float, resource: str, run: JobRun) -> JobSpec:
    return JobSpec(
        job_id,
        IntervalSchedule(seconds),
        55,
        resource,
        OverlapPolicy.REJECT,
        MissedRunPolicy.SKIP,
        run,
    )
