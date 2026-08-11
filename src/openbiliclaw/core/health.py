"""Immutable, payload-free runtime health contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class HealthStatus(StrEnum):
    """Aggregate readiness state for a component."""

    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"


class HealthIssue(StrEnum):
    """Safe diagnostic categories; exception messages are intentionally excluded."""

    COMPONENT_FAILURE = "component_failure"
    OPTIONAL_COMPONENT_FAILURE = "optional_component_failure"
    JOB_FAILURE = "job_failure"
    JOB_TIMEOUT = "job_timeout"
    SHUTDOWN_TIMEOUT = "shutdown_timeout"


class JobResult(StrEnum):
    """Payload-free outcome of a supervised job run."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class JobHealth(StrictBaseModel):
    """Bounded execution metadata for one job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    active_runs: int = Field(ge=0)
    last_result: JobResult | None = None
    runs_started: int = Field(ge=0)
    runs_completed: int = Field(ge=0)


class HealthSnapshot(StrictBaseModel):
    """Immutable health report containing no payloads or free-form errors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    component_id: str = Field(min_length=1)
    status: HealthStatus
    checked_at: AwareDatetime
    issue: HealthIssue | None = None
    jobs: tuple[JobHealth, ...] = ()
