"""Core JobSpecs for proactive recommendation work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from openbiliclaw.core.jobs import IntervalSchedule, JobSpec, MissedRunPolicy, OverlapPolicy

JobRun = Callable[[], Awaitable[None]]


def recommendation_jobs(*, replenishment: JobRun, expiry: JobRun) -> tuple[JobSpec, ...]:
    """Schedule the two independently executable recommendation operations."""
    return (
        _job("recommendation.replenishment", 120, "network", replenishment),
        _job("recommendation.expiry", 900, "database", expiry),
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
