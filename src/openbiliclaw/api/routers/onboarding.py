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
from openbiliclaw.api.threading import run_sync_port
from openbiliclaw.api.v1_models import (
    JobRunResponse,
    OnboardingProgressEvent,
    OnboardingTerminalEvent,
    job_response,
    sse_response,
)
from openbiliclaw.features.sources.domain import SourceId  # noqa: TC001
from openbiliclaw.features.system.domain import UserSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import Literal


class OnboardingStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ids: tuple[SourceId, ...] = Field(min_length=1)


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


@router.get(
    "/{run_id}/events",
    operation_id="v1_onboarding_events",
    responses=sse_response(
        {
            "progress": "OnboardingProgressEvent",
            "done": "OnboardingTerminalEvent",
            "error": "StreamErrorEvent",
        },
        description="First-run onboarding progress event stream.",
    ),
)
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
            workflow = await run_sync_port(container.onboarding.progress, run_id)
            snapshot = job_response(workflow.run)
            progress = OnboardingProgressEvent(
                root_run_id=workflow.root_run_id,
                stage=workflow.stage,
                run=snapshot,
                onboarding_complete=workflow.onboarding_complete,
            )
            yield frame("progress", progress.model_dump(mode="json"))
            status_value = snapshot.status
            succeeded = (
                workflow.stage == "feed_replenishment"
                and status_value == "succeeded"
                and workflow.onboarding_complete
            )
            terminal_status: Literal["succeeded", "failed", "cancelled"] | None = None
            if status_value == "failed":
                terminal_status = "failed"
            elif status_value == "cancelled":
                terminal_status = "cancelled"
            elif succeeded:
                terminal_status = "succeeded"
            if terminal_status is not None:
                done = OnboardingTerminalEvent(
                    root_run_id=workflow.root_run_id,
                    stage=workflow.stage,
                    run_id=snapshot.id,
                    status=terminal_status,
                    onboarding_complete=workflow.onboarding_complete,
                )
                yield frame("done", done.model_dump(mode="json"))
                return
            await asyncio.sleep(0.25)
    except asyncio.CancelledError:
        raise
    except Exception:
        if not await disconnected(request):
            yield frame("error", {"code": "onboarding_status_unavailable"})
