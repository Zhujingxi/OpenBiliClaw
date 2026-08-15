from __future__ import annotations

import asyncio
import hmac
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Request, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import EventEnvelope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

router = APIRouter(prefix="/events", tags=["events"])
_adapter: TypeAdapter[EventEnvelope] = TypeAdapter(EventEnvelope)


async def _events(dependencies: HostDependencies, after: int) -> tuple[EventEnvelope, ...]:
    if dependencies.events is None:
        return ()
    limit = dependencies.security.replay_limit
    raw = await dependencies.events.replay(after, limit)
    return tuple(_adapter.validate_python(item) for item in raw[:limit])


async def _event_stream(
    dependencies: HostDependencies,
    after: int,
    disconnected: Callable[[], Awaitable[bool]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[str]:
    reconnect_seconds = dependencies.security.reconnect_milliseconds / 1000
    cursor = after
    yield f"retry: {dependencies.security.reconnect_milliseconds}\n\n"
    while not await disconnected():
        for event in await _events(dependencies, cursor):
            cursor = event.event_id
            yield (
                f"id: {event.event_id}\nevent: {event.kind.value}\n"
                f"data: {_adapter.dump_json(event).decode()}\n\n"
            )
        if await disconnected():
            return
        # ponytail: bounded polling avoids a second subscription API; replace it
        # with an EventSource wait primitive if event latency becomes material.
        await sleep(reconnect_seconds)
        if await disconnected():
            return
        yield ": keep-alive\n\n"


@router.get(
    "/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": _adapter.json_schema()}}}},
)
async def stream(
    request: Request,
    after: int = Query(default=0, ge=0),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> StreamingResponse:
    return StreamingResponse(
        _event_stream(dependencies, after, request.is_disconnected),
        media_type="text/event-stream",
    )


async def _websocket_authorized(websocket: WebSocket, dependencies: HostDependencies) -> bool:
    policy = dependencies.security
    origin = websocket.headers.get("origin")
    if origin is not None and not policy.origin_allowed(origin):
        return False
    auth_required = policy.bearer_token is not None or policy.password_hash is not None
    if not auth_required:
        return True
    authorization = websocket.headers.get("authorization", "")
    if policy.bearer_token is not None and hmac.compare_digest(
        authorization.encode(), f"Bearer {policy.bearer_token}".encode()
    ):
        return True
    if not authorization.startswith("Bearer ") or dependencies.auth_tokens is None:
        return False
    return await dependencies.auth_tokens.verify(authorization.removeprefix("Bearer ")) is not None


@router.websocket("/ws")
async def websocket(
    websocket: WebSocket,
    after: int = Query(default=0, ge=0),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> None:
    if not await _websocket_authorized(websocket, dependencies):
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
