"""Composition-owned Assistant turn and conversation orchestration."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from openbiliclaw.assistant.dependencies import AssistantDependencies, ConversationMetadata
from openbiliclaw.assistant.models import (
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationScope,
    TurnUsage,
)
from openbiliclaw.assistant.service import AssistantService, TurnCommand
from openbiliclaw.assistant.tools import (
    AssistantIntent,
    ToolAvailability,
    ToolResultBudget,
    build_workflow_tools,
    select_tools,
)
from openbiliclaw.composition.jobs import DEFAULT_PROFILE_ID
from openbiliclaw.understanding.projections import dialogue_projection

if TYPE_CHECKING:
    from pydantic_ai import Tool

    from openbiliclaw.assistant.models import AssistantOutput
    from openbiliclaw.assistant.repository import SqliteConversationRepository
    from openbiliclaw.hosts.api.dependencies import AssistantTurnInput, HostFacade
    from openbiliclaw.understanding.service import UnderstandingService


class _AssistantToolFacade:
    """Adapt host workflows to the Assistant's narrower safe tool signatures."""

    def __init__(self, facade: HostFacade) -> None:
        self._facade = facade

    async def get_recommendations(self, limit: int) -> object:
        return await self._facade.get_recommendations(limit)

    async def search_content(self, provider_id: str, text: str, limit: int) -> object:
        return await self._facade.search_content(provider_id, text, limit)

    async def get_content_details(self, reference: str) -> object:
        return await self._facade.get_content_details(reference)

    async def show_profile(self) -> object:
        return await self._facade.show_profile(DEFAULT_PROFILE_ID)

    async def list_sources(self) -> object:
        return await self._facade.list_sources(None, 50)

    async def record_feedback(self, reference: str, kind: str) -> object:
        del reference, kind
        raise RuntimeError("Assistant feedback requires explicit UI confirmation")

    async def edit_profile(self, claim_id: str, operation: str, value: str | None) -> object:
        del claim_id, operation, value
        raise RuntimeError("Assistant profile edits require explicit UI confirmation")

    async def connect_source(self, provider_id: str) -> object:
        del provider_id
        raise RuntimeError("Assistant source connection requires a secret-free UI flow")


def assistant_workflow_tools(application: HostFacade) -> tuple[Tool[None], ...]:
    """Build the union of intent-scoped safe workflow tools without duplicates."""
    tools = build_workflow_tools(_AssistantToolFacade(application), ToolResultBudget())
    selected = {
        tool.name: tool
        for intent in AssistantIntent
        for tool in select_tools(tools, intent=intent, availability=ToolAvailability())
    }
    return tuple(selected.values())


class AssistantController:
    """Persist one device-scoped conversation around every bounded model turn."""

    def __init__(
        self,
        service: AssistantService,
        conversations: SqliteConversationRepository,
        understanding: UnderstandingService,
        application: HostFacade,
    ) -> None:
        self._service = service
        self._conversations = conversations
        self._understanding = understanding
        self._application = _AssistantToolFacade(application)

    @staticmethod
    def _scope(device_id: str) -> ConversationScope:
        return ConversationScope(local_user_id="local", device_id=device_id)

    async def turn(self, request: AssistantTurnInput, device_id: str) -> AssistantOutput:
        now = datetime.now(UTC)
        scope = self._scope(device_id)
        conversation = await self._conversations.get_conversation(request.conversation_id, scope)
        if conversation is None:
            conversation = Conversation(
                conversation_id=request.conversation_id,
                scope=scope,
                created_at=now,
                updated_at=now,
            )
            await self._conversations.put_conversation(conversation)
        profile = dialogue_projection(await self._understanding.profile(DEFAULT_PROFILE_ID))
        result = await self._service.run_turn(
            TurnCommand(
                text=request.text,
                deps=AssistantDependencies(
                    application=self._application,
                    profile=profile,
                    locale=request.locale,
                    conversation=ConversationMetadata(request.conversation_id, scope),
                ),
            )
        )
        user_key = (
            f"turn:{request.conversation_id}:{hashlib.sha256(request.text.encode()).hexdigest()}"
        )
        await self._conversations.append_message(
            request.conversation_id,
            self._message(ConversationRole.USER, request.text, user_key, now),
        )
        output_text = result.output.model_dump_json()
        await self._conversations.append_message(
            request.conversation_id,
            self._message(
                ConversationRole.ASSISTANT,
                output_text,
                user_key + ":assistant",
                datetime.now(UTC),
                usage=TurnUsage(
                    request_count=result.usage.requests,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                ),
            ),
        )
        await self._conversations.put_conversation(
            conversation.model_copy(update={"updated_at": datetime.now(UTC)})
        )
        return result.output

    async def conversation(self, conversation_id: str, device_id: str) -> Conversation:
        conversation = await self._conversations.get_conversation(
            conversation_id, self._scope(device_id)
        )
        if conversation is None:
            from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode

            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "conversation not found")
        return conversation

    async def messages(
        self, conversation_id: str, device_id: str, limit: int
    ) -> tuple[ConversationMessage, ...]:
        await self.conversation(conversation_id, device_id)
        return await self._conversations.messages(conversation_id, limit=limit)

    @staticmethod
    def _message(
        role: ConversationRole,
        content: str,
        idempotency_key: str,
        created_at: datetime,
        *,
        usage: TurnUsage | None = None,
    ) -> ConversationMessage:
        identity = hashlib.sha256(f"{role.value}:{idempotency_key}".encode()).hexdigest()[:32]
        return ConversationMessage(
            message_id=f"msg_{identity}",
            role=role,
            content=content,
            created_at=created_at,
            idempotency_key=idempotency_key[:200],
            usage=usage,
        )
