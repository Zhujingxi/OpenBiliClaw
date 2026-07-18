"""Direct interactive chat SSE route."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - Pydantic resolves the field at runtime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse  # noqa: TC002 - FastAPI route response
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.api.dependencies import ApplicationContainer, Container, require_access
from openbiliclaw.api.sse import frame, response
from openbiliclaw.api.threading import run_sync_port
from openbiliclaw.api.v1_models import sse_response
from openbiliclaw.features.chat.service import ChatHistoryPage

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    message: str = Field(min_length=1, max_length=20_000)
    learn: bool = False


router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_access)])


@router.get(
    "/{conversation_id}",
    operation_id="v1_chat_history",
    response_model=ChatHistoryPage,
)
async def chat_history(
    conversation_id: UUID,
    container: Container,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> ChatHistoryPage:
    return await run_sync_port(
        container.chat.history,
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/stream",
    operation_id="v1_chat_stream",
    responses=sse_response(
        {
            "delta": "ChatChunk",
            "done": "ChatDoneEvent",
            "error": "StreamErrorEvent",
        },
        description="Interactive chat event stream.",
    ),
)
def stream_chat(
    payload: ChatRequest,
    request: Request,
    container: Container,
) -> StreamingResponse:
    return response(_chat_events(payload, request, container))


async def _chat_events(
    payload: ChatRequest,
    request: Request,
    container: ApplicationContainer,
) -> AsyncGenerator[str]:
    if await request.is_disconnected():
        return
    try:
        async with aclosing(
            container.chat.stream(
                conversation_id=payload.conversation_id,
                message=payload.message,
                learn=payload.learn,
            )
        ) as chunks:
            async for chunk in chunks:
                if await request.is_disconnected():
                    return
                yield frame(chunk.kind.value, chunk.model_dump(mode="json"))
    except asyncio.CancelledError:
        raise
    except Exception:
        if await request.is_disconnected():
            return
        yield frame("error", {"code": "chat_unavailable"})
        yield frame("done", {"status": "failed"})
