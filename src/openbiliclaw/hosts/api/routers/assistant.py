from __future__ import annotations

import asyncio
from contextlib import aclosing
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter

from openbiliclaw.assistant.agent import ASSISTANT_POLICY
from openbiliclaw.assistant.models import AssistantStreamError

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import (
    AssistantTurnLifecycleEvent,
    AssistantTurnRequest,
    AssistantTurnResponse,
    ConversationResponse,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

router = APIRouter(prefix="/assistant", tags=["assistant"])
_event_adapter: TypeAdapter[AssistantTurnLifecycleEvent] = TypeAdapter(AssistantTurnLifecycleEvent)
_ASSISTANT_STREAM_TIMEOUT_SECONDS = float(ASSISTANT_POLICY.timeout_seconds + 5)


async def _prepend_event(
    first: AssistantTurnLifecycleEvent,
    events: AsyncIterator[AssistantTurnLifecycleEvent],
) -> AsyncIterator[AssistantTurnLifecycleEvent]:
    async with aclosing(cast("AsyncGenerator[AssistantTurnLifecycleEvent, None]", events)):
        yield first
        async for event in events:
            yield event


async def _turn_event_stream(
    events: AsyncIterator[AssistantTurnLifecycleEvent],
    disconnected: Callable[[], Awaitable[bool]],
    timeout_seconds: float,
) -> AsyncIterator[str]:
    try:
        async with aclosing(cast("AsyncGenerator[AssistantTurnLifecycleEvent, None]", events)):
            async with asyncio.timeout(timeout_seconds):
                async for event in events:
                    if await disconnected():
                        return
                    yield (
                        f"event: {event.kind}\ndata: {_event_adapter.dump_json(event).decode()}\n\n"
                    )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        error = AssistantStreamError(code="temporary_failure", message="assistant stream timed out")
        yield f"event: error\ndata: {_event_adapter.dump_json(error).decode()}\n\n"
    except Exception:
        error = AssistantStreamError(
            code="temporary_failure", message="assistant stream failed safely"
        )
        yield f"event: error\ndata: {_event_adapter.dump_json(error).decode()}\n\n"


@router.post("/turns", response_model=AssistantTurnResponse)
async def turn(
    body: AssistantTurnRequest,
    device_id: str = Header(alias="X-Device-ID", min_length=1, max_length=128),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> AssistantTurnResponse:
    return AssistantTurnResponse(output=await dependencies.facade.assistant_turn(body, device_id))


@router.post(
    "/turns/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": _event_adapter.json_schema()}}}},
)
async def stream_turn(
    body: AssistantTurnRequest,
    request: Request,
    device_id: str = Header(alias="X-Device-ID", min_length=1, max_length=128),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> StreamingResponse:
    events = dependencies.facade.assistant_turn_stream(body, device_id)
    first = await anext(events)
    return StreamingResponse(
        _turn_event_stream(
            _prepend_event(first, events),
            request.is_disconnected,
            _ASSISTANT_STREAM_TIMEOUT_SECONDS,
        ),
        media_type="text/event-stream",
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def conversation(
    conversation_id: str,
    device_id: str = Header(alias="X-Device-ID", min_length=1, max_length=128),
    message_limit: int | None = Query(default=None, ge=1, le=100),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> ConversationResponse:
    return ConversationResponse(
        conversation=await dependencies.facade.conversation(conversation_id, device_id),
        messages=await dependencies.facade.conversation_messages(
            conversation_id, device_id, message_limit
        ),
    )
