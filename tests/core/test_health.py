from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from openbiliclaw.core.health import (
    HealthIssue,
    HealthSnapshot,
    HealthStatus,
    JobHealth,
    JobResult,
)


def test_health_snapshot_is_frozen_and_contains_only_structured_diagnostics() -> None:
    snapshot = HealthSnapshot(
        component_id="runtime",
        status=HealthStatus.DEGRADED,
        checked_at=datetime.now(UTC),
        issue=HealthIssue.COMPONENT_FAILURE,
        jobs=(
            JobHealth(
                job_id="refresh",
                active_runs=0,
                last_result=JobResult.ERROR,
                runs_started=2,
                runs_completed=1,
            ),
        ),
    )

    assert snapshot.jobs[0].last_result is JobResult.ERROR
    assert "password" not in snapshot.model_dump_json()
    with pytest.raises(ValidationError):
        snapshot.status = HealthStatus.HEALTHY


def test_health_contract_has_no_free_form_exception_field() -> None:
    with pytest.raises(ValidationError):
        HealthSnapshot.model_validate(
            {
                "component_id": "runtime",
                "status": "unhealthy",
                "checked_at": datetime.now(UTC),
                "error": "api_key=secret",
            }
        )
