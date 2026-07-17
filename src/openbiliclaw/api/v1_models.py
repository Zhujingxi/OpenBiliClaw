"""Transport-owned request, response, and stream schemas shared by v1 routers."""

from __future__ import annotations

from typing import Any, Literal, TypedDict
from uuid import UUID  # noqa: TC003 - Pydantic resolves runtime fields

from fastapi.encoders import jsonable_encoder
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from openbiliclaw.features.activity.domain import ProfileSignal  # noqa: TC001
from openbiliclaw.features.chat.service import ChatChunk
from openbiliclaw.features.feed.domain import Interaction  # noqa: TC001

ModelAlias = Literal["obc-interactive", "obc-analysis", "obc-embedding"]
JobStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
OnboardingStage = Literal["source_sync", "profile_projection", "feed_replenishment"]


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


class EventIngestResponse(BaseModel):
    """Accepted event identity and the normalized profile signals it produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    signals: tuple[ProfileSignal, ...]


class InteractionResponse(BaseModel):
    """Persisted interaction and its deterministic profile signal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    interaction: Interaction
    signal: ProfileSignal


class EventIngestResult(TypedDict):
    """Typed handler value validated against :class:`EventIngestResponse`."""

    event_id: UUID
    signals: tuple[ProfileSignal, ...]


class InteractionResult(TypedDict):
    """Typed handler value validated against :class:`InteractionResponse`."""

    interaction: Interaction
    signal: ProfileSignal


class StreamErrorEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str


class StreamTerminalEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["succeeded", "failed", "cancelled"]
    id: UUID | None = None


class OnboardingProgressEvent(BaseModel):
    """Current persisted stage and its concrete child job run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root_run_id: UUID
    stage: OnboardingStage
    run: JobRunResponse
    onboarding_complete: bool


class OnboardingTerminalEvent(BaseModel):
    """Terminal state propagated from the current durable onboarding child."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root_run_id: UUID
    stage: OnboardingStage
    run_id: UUID
    status: Literal["succeeded", "failed", "cancelled"]
    onboarding_complete: bool


class ChatDoneEvent(BaseModel):
    """Union-shaped payload accepted for normal and failed chat terminals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["done"] | None = None
    content: str | None = None
    turn_id: UUID | None = None
    status: Literal["failed"] | None = None


SSE_COMPONENT_MODELS = (
    ChatChunk,
    ChatDoneEvent,
    JobRunResponse,
    OnboardingProgressEvent,
    OnboardingTerminalEvent,
    StreamErrorEvent,
    StreamTerminalEvent,
)


def sse_response(
    event_schemas: dict[str, str], *, description: str
) -> dict[int | str, dict[str, Any]]:
    """Return an OpenAPI response with typed vendor metadata for SSE consumers."""

    return {
        200: {
            "description": description,
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "x-sse-events": {
                        event: {"schema": {"$ref": f"#/components/schemas/{model_name}"}}
                        for event, model_name in event_schemas.items()
                    },
                }
            },
        }
    }


def job_response(value: object) -> JobRunResponse:
    return JobRunResponse.model_validate(jsonable_encoder(value))


def terminal_job(status: object) -> bool:
    return str(status) in {"succeeded", "failed", "cancelled"}


__all__ = [
    "AIHealthResponse",
    "AliasHealthResponse",
    "ChatDoneEvent",
    "EventIngestResult",
    "EventIngestResponse",
    "InteractionResult",
    "InteractionResponse",
    "JobRunResponse",
    "OnboardingProgressEvent",
    "OnboardingTerminalEvent",
    "SSE_COMPONENT_MODELS",
    "StreamErrorEvent",
    "StreamTerminalEvent",
    "job_response",
    "sse_response",
    "terminal_job",
]
