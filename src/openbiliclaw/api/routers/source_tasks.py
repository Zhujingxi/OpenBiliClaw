"""Generic extension-assisted source task claim and completion."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI resolves route fields at runtime

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.features.sources.domain import (
    ClaimedSourceTask,
    SourceId,
    SourceTaskCompletion,
)
from openbiliclaw.features.sources.service import validate_source_task_payload


class CompleteSourceTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_token: str = Field(min_length=20, max_length=100)
    result: dict[str, Any]


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
        task = container.source_tasks.claim(source_id.value)
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
    result = validate_source_task_payload(payload.result)
    completed = container.source_tasks.complete(task_id, payload.lease_token, result)
    return SourceTaskCompletion.model_validate(jsonable_encoder(completed))
