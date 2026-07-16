"""Small standards-compliant Server-Sent Events helpers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, Iterable, Mapping

    from fastapi import Request


def frame(event: str, data: Mapping[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def disconnected(request: Request) -> bool:
    return await request.is_disconnected()


def response(iterator: AsyncIterable[str] | Iterable[str]) -> StreamingResponse:
    return StreamingResponse(
        iterator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["disconnected", "frame", "response"]
