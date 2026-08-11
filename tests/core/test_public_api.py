from __future__ import annotations

from openbiliclaw.core import (
    CronSchedule,
    HealthIssue,
    IntervalSchedule,
    JobDecision,
    JobResult,
    MissedRunPolicy,
    OverlapPolicy,
)


def test_core_reexports_policy_and_health_contracts() -> None:
    assert CronSchedule("0 * * * *").expression == "0 * * * *"
    assert IntervalSchedule(1).seconds == 1
    assert OverlapPolicy.REJECT.value == "reject"
    assert MissedRunPolicy.SKIP.value == "skip"
    assert JobDecision.RUN.value == "run"
    assert JobResult.SUCCESS.value == "success"
    assert HealthIssue.JOB_TIMEOUT.value == "job_timeout"
