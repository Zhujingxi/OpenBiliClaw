"""Transport-owned read schemas shared by v1 routers."""

from __future__ import annotations

from typing import Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves runtime fields

from fastapi.encoders import jsonable_encoder
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

ModelAlias = Literal["obc-interactive", "obc-analysis", "obc-embedding"]
JobStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]


class AliasHealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alias: ModelAlias
    available: bool
    state: Literal["healthy", "degraded", "unavailable"]
    reason: str | None = None


class AIHealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    proxy_reachable: bool
    aliases: tuple[AliasHealthResponse, ...]


class JobRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: UUID
    job_name: str
    idempotency_key: str
    status: JobStatus
    priority: int
    progress: float = Field(ge=0, le=1)
    attempts: int = Field(ge=0)
    error: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    dispatched_at: AwareDatetime | None = None


def job_response(value: object) -> JobRunResponse:
    return JobRunResponse.model_validate(jsonable_encoder(value))


def terminal_job(status: object) -> bool:
    return str(status) in {"succeeded", "failed", "cancelled"}


__all__ = [
    "AIHealthResponse",
    "AliasHealthResponse",
    "JobRunResponse",
    "job_response",
    "terminal_job",
]
