from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.core.jobs import (
    CronSchedule,
    IntervalSchedule,
    JobDecision,
    JobScheduler,
    JobSpec,
    MissedRunPolicy,
    OverlapPolicy,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


async def _work() -> None:
    return None


class FakeSchedulerBackend:
    def __init__(self) -> None:
        self.callbacks: dict[str, Callable[[int], None]] = {}
        self.started = False
        self.start_calls = 0
        self.shutdown_calls = 0

    def add_job(
        self,
        job_id: str,
        schedule: IntervalSchedule | CronSchedule,
        callback: Callable[[int], None],
    ) -> None:
        del schedule
        self.callbacks[job_id] = callback

    def start(self) -> None:
        self.start_calls += 1
        self.started = True

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.started = False

    def fire(self, job_id: str, *, missed_runs: int = 0) -> None:
        self.callbacks[job_id](missed_runs)


def test_fake_scheduler_only_delegates_registered_job_admission() -> None:
    backend = FakeSchedulerBackend()
    decisions: list[tuple[str, int]] = []

    def trigger(spec: JobSpec, missed_runs: int) -> JobDecision:
        decisions.append((spec.job_id, missed_runs))
        return JobDecision.RUN

    driver = JobScheduler(backend, trigger)
    spec = JobSpec(
        job_id="refresh",
        schedule=IntervalSchedule(seconds=60),
        timeout_seconds=5,
        resource="network",
        overlap_policy=OverlapPolicy.REJECT,
        missed_run_policy=MissedRunPolicy.RUN_ONCE,
        run=_work,
    )
    driver.register(spec)
    driver.start()
    backend.fire("refresh", missed_runs=2)
    driver.stop()

    assert decisions == [("refresh", 2)]
    assert not backend.started
    with pytest.raises(ValueError, match="duplicate"):
        driver.register(spec)


def test_scheduler_start_stop_are_idempotent_and_registration_closes_at_start() -> None:
    backend = FakeSchedulerBackend()
    driver = JobScheduler(backend, lambda _spec, _missed: JobDecision.RUN)
    spec = JobSpec(
        job_id="refresh",
        schedule=IntervalSchedule(seconds=60),
        timeout_seconds=5,
        resource="network",
        overlap_policy=OverlapPolicy.REJECT,
        missed_run_policy=MissedRunPolicy.SKIP,
        run=_work,
    )
    driver.register(spec)

    driver.start()
    driver.start()
    with pytest.raises(RuntimeError, match="after scheduler start"):
        driver.register(
            JobSpec(
                job_id="other",
                schedule=IntervalSchedule(seconds=60),
                timeout_seconds=5,
                resource="network",
                overlap_policy=OverlapPolicy.REJECT,
                missed_run_policy=MissedRunPolicy.SKIP,
                run=_work,
            )
        )
    driver.stop()
    driver.stop()

    assert backend.start_calls == 1
    assert backend.shutdown_calls == 1


@pytest.mark.parametrize(
    ("overlap", "missed", "active_runs", "missed_runs", "expected"),
    [
        (OverlapPolicy.REJECT, MissedRunPolicy.SKIP, 1, 0, JobDecision.REJECT_OVERLAP),
        (OverlapPolicy.ALLOW, MissedRunPolicy.SKIP, 1, 0, JobDecision.RUN),
        (OverlapPolicy.REJECT, MissedRunPolicy.SKIP, 0, 1, JobDecision.SKIP_MISSED),
        (OverlapPolicy.ALLOW, MissedRunPolicy.SKIP, 1, 2, JobDecision.SKIP_MISSED),
        (OverlapPolicy.ALLOW, MissedRunPolicy.RUN_ONCE, 1, 2, JobDecision.RUN),
        (OverlapPolicy.REJECT, MissedRunPolicy.RUN_ONCE, 0, 4, JobDecision.RUN),
        (OverlapPolicy.REJECT, MissedRunPolicy.RUN_ONCE, 1, 4, JobDecision.REJECT_OVERLAP),
        (OverlapPolicy.REJECT, MissedRunPolicy.SKIP, 0, 0, JobDecision.RUN),
    ],
)
def test_job_admission_policy_table(
    overlap: OverlapPolicy,
    missed: MissedRunPolicy,
    active_runs: int,
    missed_runs: int,
    expected: JobDecision,
) -> None:
    spec = JobSpec(
        job_id="refresh",
        schedule=IntervalSchedule(seconds=60),
        timeout_seconds=5,
        resource="network",
        overlap_policy=overlap,
        missed_run_policy=missed,
        run=_work,
    )

    assert spec.decide(active_runs=active_runs, missed_runs=missed_runs) is expected


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: IntervalSchedule(seconds=0),
        lambda: CronSchedule(expression="not a cron"),
        lambda: JobSpec(
            job_id="",
            schedule=IntervalSchedule(seconds=1),
            timeout_seconds=1,
            resource="default",
            overlap_policy=OverlapPolicy.REJECT,
            missed_run_policy=MissedRunPolicy.SKIP,
            run=_work,
        ),
    ],
)
def test_job_contracts_reject_invalid_values(constructor: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        constructor()


def test_job_decision_rejects_negative_counters() -> None:
    spec = JobSpec(
        job_id="counts",
        schedule=IntervalSchedule(seconds=1),
        timeout_seconds=1,
        resource="default",
        overlap_policy=OverlapPolicy.REJECT,
        missed_run_policy=MissedRunPolicy.SKIP,
        run=_work,
    )

    with pytest.raises(ValueError, match="must not be negative"):
        spec.decide(active_runs=-1, missed_runs=0)
    with pytest.raises(ValueError, match="must not be negative"):
        spec.decide(active_runs=0, missed_runs=-1)


def test_job_callable_is_typed_as_awaitable() -> None:
    run: Callable[[], Awaitable[None]] = _work
    spec = JobSpec(
        job_id="typed",
        schedule=CronSchedule(expression="0 * * * *"),
        timeout_seconds=3,
        resource="default",
        overlap_policy=OverlapPolicy.REJECT,
        missed_run_policy=MissedRunPolicy.SKIP,
        run=run,
    )

    assert spec.run is run
