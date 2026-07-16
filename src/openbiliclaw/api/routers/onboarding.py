"""First-run bootstrap scheduling and progress streaming."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - Pydantic and FastAPI resolve route fields

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse  # noqa: TC002 - FastAPI route response
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.api.dependencies import (
    ApplicationContainer,
    Container,
    require_onboarding_access,
)
from openbiliclaw.api.sse import disconnected, frame, response
from openbiliclaw.api.v1_models import JobRunResponse, job_response, terminal_job
from openbiliclaw.features.sources.domain import SourceId  # noqa: TC001
from openbiliclaw.features.system.domain import UserSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class OnboardingStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ids: tuple[SourceId, ...] = Field(default_factory=tuple)


router = APIRouter(
    prefix="/onboarding",
    tags=["onboarding"],
    dependencies=[Depends(require_onboarding_access)],
)


@router.get("", operation_id="v1_onboarding_get", response_model=UserSettings)
def onboarding_status(
    container: Container,
) -> UserSettings:
    return container.onboarding.status()


@router.post(
    "/start",
    operation_id="v1_onboarding_start",
    response_model=JobRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_onboarding(
    payload: OnboardingStart,
    container: Container,
) -> JobRunResponse:
    return job_response(
        container.onboarding.start(tuple(source.value for source in payload.source_ids))
    )


@router.get("/{run_id}/events", operation_id="v1_onboarding_events")
def onboarding_events(
    run_id: UUID,
    request: Request,
    container: Container,
) -> StreamingResponse:
    return response(_progress_events(run_id, request, container))


async def _progress_events(
    run_id: UUID, request: Request, container: ApplicationContainer
) -> AsyncIterator[str]:
    try:
        while not await disconnected(request):
            snapshot = container.jobs.inspect(run_id)
            yield frame("progress", snapshot.model_dump(mode="json"))
            if terminal_job(snapshot.status):
                yield frame("done", {"id": str(snapshot.id), "status": snapshot.status.value})
                return
            await asyncio.sleep(0.25)
    except asyncio.CancelledError:
        raise
    except Exception:
        if not await disconnected(request):
            yield frame("error", {"code": "onboarding_status_unavailable"})
            yield frame("done", {"status": "failed"})
