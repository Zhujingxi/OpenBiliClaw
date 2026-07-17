"""Business job scheduling, inspection, cancellation, and progress SSE."""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import TYPE_CHECKING, Literal
from uuid import UUID  # noqa: TC003 - Pydantic and FastAPI resolve route fields

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse  # noqa: TC002 - FastAPI route response
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.api.dependencies import ApplicationContainer, Container, require_access
from openbiliclaw.api.sse import frame, response
from openbiliclaw.api.threading import run_sync_port
from openbiliclaw.api.v1_models import JobRunResponse, job_response, sse_response, terminal_job

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


JobName = Literal["source_sync", "profile_projection", "feed_replenishment", "cleanup"]


class JobPriorityLane(StrEnum):
    """Closed user-facing names for the three queue priorities."""

    INTERACTIVE = "interactive"
    USER_TRIGGERED = "user-triggered"
    SCHEDULED = "scheduled"


_PRIORITY_BY_LANE = {
    JobPriorityLane.INTERACTIVE: 100,
    JobPriorityLane.USER_TRIGGERED: 50,
    JobPriorityLane.SCHEDULED: 10,
}


class ScheduleJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: JobName
    idempotency_key: str = Field(min_length=1, max_length=150)
    priority: JobPriorityLane | None = None


router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_access)])


@router.post("", operation_id="v1_jobs_schedule", response_model=JobRunResponse, status_code=202)
def schedule_job(
    payload: ScheduleJob,
    container: Container,
) -> JobRunResponse:
    return job_response(
        container.jobs.schedule(
            payload.job_name,
            idempotency_key=payload.idempotency_key,
            priority=_PRIORITY_BY_LANE[payload.priority] if payload.priority is not None else None,
        )
    )


@router.get("", operation_id="v1_jobs_list", response_model=tuple[JobRunResponse, ...])
def list_jobs(
    container: Container,
    limit: int = Query(default=100, ge=1, le=500),
) -> tuple[JobRunResponse, ...]:
    return tuple(job_response(run) for run in container.jobs.list(limit=limit))


@router.get("/{run_id}", operation_id="v1_jobs_get", response_model=JobRunResponse)
def get_job(run_id: UUID, container: Container) -> JobRunResponse:
    return job_response(container.jobs.inspect(run_id))


@router.delete("/{run_id}", operation_id="v1_jobs_cancel", response_model=JobRunResponse)
def cancel_job(run_id: UUID, container: Container) -> JobRunResponse:
    return job_response(container.jobs.cancel(run_id))


@router.get(
    "/{run_id}/events",
    operation_id="v1_jobs_events",
    responses=sse_response(
        {
            "progress": "JobRunResponse",
            "done": "StreamTerminalEvent",
            "error": "StreamErrorEvent",
        },
        description="Business job progress event stream.",
    ),
)
def job_events(
    run_id: UUID,
    request: Request,
    container: Container,
) -> StreamingResponse:
    return response(_job_events(run_id, request, container))


async def _job_events(
    run_id: UUID, request: Request, container: ApplicationContainer
) -> AsyncIterator[str]:
    try:
        while not await request.is_disconnected():
            snapshot = await run_sync_port(container.jobs.inspect, run_id)
            yield frame("progress", snapshot.model_dump(mode="json"))
            if terminal_job(snapshot.status):
                yield frame("done", {"id": str(snapshot.id), "status": snapshot.status.value})
                return
            await asyncio.sleep(0.25)
    except asyncio.CancelledError:
        raise
    except Exception:
        if not await request.is_disconnected():
            yield frame("error", {"code": "job_status_unavailable"})
            yield frame("done", {"status": "failed"})
