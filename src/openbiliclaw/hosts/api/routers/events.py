from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import EventEnvelope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

router = APIRouter(prefix="/events", tags=["events"])
_adapter: TypeAdapter[EventEnvelope] = TypeAdapter(EventEnvelope)


async def _events(dependencies: HostDependencies, after: int) -> tuple[EventEnvelope, ...]:
    if dependencies.events is None:
        return ()
    limit = dependencies.security.replay_limit
    raw = await dependencies.events.replay(after, limit)
    return tuple(_adapter.validate_python(item) for item in raw[:limit])


@router.get(
    "/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": _adapter.json_schema()}}}},
)
async def stream(
    after: int = Query(default=0, ge=0),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        yield f"retry: {dependencies.security.reconnect_milliseconds}\n"
        for event in await _events(dependencies, after):
            yield (
                f"id: {event.event_id}\nevent: {event.kind.value}\n"
                f"data: {_adapter.dump_json(event).decode()}\n\n"
            )

    return StreamingResponse(body(), media_type="text/event-stream")


def _websocket_authorized(websocket: WebSocket, dependencies: HostDependencies) -> bool:
    policy = dependencies.security
    origin = websocket.headers.get("origin")
    if origin is not None and not policy.origin_allowed(origin):
        return False
    if policy.bearer_token is None:
        return True
    return websocket.headers.get("authorization") == f"Bearer {policy.bearer_token}"


@router.websocket("/ws")
async def websocket(
    websocket: WebSocket,
    after: int = Query(default=0, ge=0),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> None:
    if not _websocket_authorized(websocket, dependencies):
        await websocket.close(code=1008, reason="authentication or origin rejected")
        return
    try:
        await asyncio.wait_for(dependencies.websocket_slots.acquire(), timeout=0.001)
    except TimeoutError:
        await websocket.close(code=1013, reason="subscriber limit reached")
        return
    try:
        await websocket.accept()
        for event in await _events(dependencies, after):
            await websocket.send_bytes(_adapter.dump_json(event))
        await websocket.close(code=1000)
    finally:
        dependencies.websocket_slots.release()
