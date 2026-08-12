from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import (
    AssistantTurnRequest,
    AssistantTurnResponse,
    ConversationResponse,
)

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/turns", response_model=AssistantTurnResponse)
async def turn(
    body: AssistantTurnRequest,
    device_id: str = Header(alias="X-Device-ID", min_length=1, max_length=128),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> AssistantTurnResponse:
    return AssistantTurnResponse(output=await dependencies.facade.assistant_turn(body, device_id))


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def conversation(
    conversation_id: str,
    device_id: str = Header(alias="X-Device-ID", min_length=1, max_length=128),
    message_limit: int = Query(default=20, ge=1, le=100),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> ConversationResponse:
    return ConversationResponse(
        conversation=await dependencies.facade.conversation(conversation_id, device_id),
        messages=await dependencies.facade.conversation_messages(
            conversation_id, device_id, message_limit
        ),
    )
