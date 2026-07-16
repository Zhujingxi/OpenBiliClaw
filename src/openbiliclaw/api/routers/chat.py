"""Direct interactive chat SSE route."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - Pydantic resolves the field at runtime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse  # noqa: TC002 - FastAPI route response
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.api.dependencies import ApplicationContainer, Container, require_access
from openbiliclaw.api.sse import frame, response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    message: str = Field(min_length=1, max_length=20_000)
    learn: bool = False


router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_access)])


@router.post("/stream", operation_id="v1_chat_stream")
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
) -> AsyncIterator[str]:
    if await request.is_disconnected():
        return
    try:
        async for chunk in container.chat.stream(
            conversation_id=payload.conversation_id,
            message=payload.message,
            learn=payload.learn,
        ):
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
