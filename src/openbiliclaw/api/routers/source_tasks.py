"""Generic extension-assisted source task claim and completion."""

from __future__ import annotations

import asyncio
from uuid import UUID  # noqa: TC003 - FastAPI resolves route fields at runtime

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.api.threading import run_sync_port
from openbiliclaw.features.sources.domain import (
    BrowserOperationResultValue,
    ClaimedSourceTask,
    SourceId,
    SourceTaskCompletion,
    SourceTaskFailure,
)


class CompleteSourceTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_token: str = Field(min_length=20, max_length=100)
    result: BrowserOperationResultValue | None = Field(default=None, discriminator="operation")
    failure: SourceTaskFailure | None = None

    @model_validator(mode="after")
    def exactly_one_outcome(self) -> CompleteSourceTask:
        if (self.result is None) == (self.failure is None):
            raise ValueError("exactly one source task result or failure is required")
        return self


router = APIRouter(
    prefix="/source-tasks", tags=["source-tasks"], dependencies=[Depends(require_access)]
)


@router.get(
    "/claim",
    operation_id="v1_source_tasks_claim",
    response_model=ClaimedSourceTask | None,
    responses={204: {"description": "No task available"}},
)
async def claim_source_task(
    request: Request,
    source_id: SourceId,
    container: Container,
    wait_seconds: float = Query(default=20, ge=0, le=30),
) -> object:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    while True:
        task = await run_sync_port(container.source_tasks.claim, source_id.value)
        if task is not None:
            return task
        if loop.time() >= deadline or await request.is_disconnected():
            return Response(status_code=204)
        await asyncio.sleep(min(0.25, max(0, deadline - loop.time())))


@router.post(
    "/{task_id}/complete",
    operation_id="v1_source_tasks_complete",
    response_model=SourceTaskCompletion,
)
def complete_source_task(
    task_id: UUID,
    payload: CompleteSourceTask,
    container: Container,
) -> object:
    if payload.failure is not None:
        completed = container.source_tasks.fail(
            task_id,
            payload.lease_token,
            code=payload.failure.code,
            error_type=payload.failure.error_type,
        )
    else:
        assert payload.result is not None
        completed = container.source_tasks.complete(task_id, payload.lease_token, payload.result)
    return SourceTaskCompletion.model_validate(jsonable_encoder(completed))
