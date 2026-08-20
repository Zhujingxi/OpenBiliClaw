"""Composition-owned Assistant turn and conversation orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from openbiliclaw.ai.runtime.errors import AIRuntimeError
from openbiliclaw.ai.runtime.execution import (
    RuntimeRunFinished,
    RuntimeTextDelta,
    RuntimeToolFinished,
    RuntimeToolStarted,
)
from openbiliclaw.ai.runtime.history import MessageAuditError, ToolResultTooLargeError
from openbiliclaw.application.content_actions import PendingAction, ProposeProfileRevisionCommand
from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.assistant.dependencies import AssistantDependencies, ConversationMetadata
from openbiliclaw.assistant.history import render_assistant_output
from openbiliclaw.assistant.models import (
    AssistantLifecycleEvent,
    AssistantStreamError,
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationScope,
    ReasoningDelta,
    ReasoningFinished,
    ReasoningStarted,
    ResponseDelta,
    ToolCallSummary,
    ToolFinished,
    ToolStarted,
    TurnFinished,
    TurnStarted,
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
from openbiliclaw.understanding.overrides import OverrideOperation
from openbiliclaw.understanding.projections import dialogue_projection

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai import Tool

    from openbiliclaw.application.reads import RecommendationsResult
    from openbiliclaw.assistant.models import AssistantOutput
    from openbiliclaw.assistant.repository import SqliteConversationRepository
    from openbiliclaw.hosts.api.dependencies import AssistantTurnInput, HostFacade
    from openbiliclaw.understanding.service import UnderstandingService


def assistant_recommendation_context(result: RecommendationsResult) -> dict[str, object]:
    """Project recommendations to model-visible titles and links without internal IDs."""

    return {
        "items": tuple(
            {
                "title": item.card.title,
                "canonical_url": item.ref.canonical_url,
                "summary": item.card.summary,
                "reason": item.reason,
            }
            for item in result.items
        )
    }


class _AssistantToolFacade:
    """Adapt host workflows to the Assistant's narrower safe tool signatures."""

    def __init__(self, facade: HostFacade) -> None:
        self._facade = facade

    async def get_recommendations(self, limit: int) -> object:
        result = await self._facade.get_recommendations(limit)
        return assistant_recommendation_context(result)

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

    async def propose_profile_revision(
        self, field: str, operation: str, value: str | None, rationale: str
    ) -> PendingAction:
        identity = hashlib.sha256(
            json.dumps(
                (DEFAULT_PROFILE_ID, field, operation, value),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return await self._facade.propose_profile_revision(
            ProposeProfileRevisionCommand(
                idempotency_key=f"assistant:profile:{identity}",
                profile_id=DEFAULT_PROFILE_ID,
                account_id="local",
                user_id="local",
                field=field,
                operation=OverrideOperation(operation),
                value=value,
                rationale=rationale,
            )
        )

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
        """Consume the canonical lifecycle workflow for non-streaming callers."""

        async for event in self.stream_turn(request, device_id):
            if isinstance(event, TurnFinished):
                return event.output
            if isinstance(event, AssistantStreamError):
                raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, event.message)
        raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "assistant turn incomplete")

    async def stream_turn(
        self, request: AssistantTurnInput, device_id: str
    ) -> AsyncIterator[AssistantLifecycleEvent]:
        """Run, persist, and expose one typed Assistant lifecycle."""

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
        command = TurnCommand(
            text=request.text,
            deps=AssistantDependencies(
                application=self._application,
                profile=profile,
                locale=request.locale,
                conversation=ConversationMetadata(request.conversation_id, scope),
            ),
        )
        prepared = self._service.prepare_turn(
            command, await self._conversations.all_messages(request.conversation_id)
        )
        yield TurnStarted(context_meter=prepared.context_meter)
        reasoning_open = False
        tool_calls: list[ToolCallSummary] = []
        try:
            async for runtime_event in self._service.stream_turn(prepared):
                if isinstance(runtime_event, RuntimeTextDelta):
                    if runtime_event.kind != "reasoning_delta":
                        continue  # Structured Assistant output is exposed only after validation.
                    if not reasoning_open:
                        reasoning_open = True
                        yield ReasoningStarted()
                    yield ReasoningDelta(delta=runtime_event.text)
                elif isinstance(runtime_event, RuntimeToolStarted):
                    if reasoning_open:
                        reasoning_open = False
                        yield ReasoningFinished()
                    yield ToolStarted(name=self._friendly_tool_name(runtime_event.tool_name))
                elif isinstance(runtime_event, RuntimeToolFinished):
                    friendly_name = self._friendly_tool_name(runtime_event.tool_name)
                    summary = f"{friendly_name} {runtime_event.summary.lower()}."
                    tool_calls.append(
                        ToolCallSummary(
                            tool_name=friendly_name,
                            outcome=runtime_event.status,
                            safe_summary=summary,
                        )
                    )
                    yield ToolFinished(
                        name=friendly_name,
                        status=runtime_event.status,
                        summary=summary,
                    )
                elif isinstance(runtime_event, RuntimeRunFinished):
                    if reasoning_open:
                        reasoning_open = False
                        yield ReasoningFinished()
                    usage = TurnUsage(
                        request_count=runtime_event.result.usage.requests,
                        input_tokens=runtime_event.result.usage.input_tokens,
                        output_tokens=runtime_event.result.usage.output_tokens,
                    )
                    await self._persist_turn(
                        conversation,
                        request.text,
                        runtime_event.result.output,
                        usage,
                        tuple(tool_calls),
                        now,
                    )
                    visible = render_assistant_output(runtime_event.result.output)
                    yield ResponseDelta(delta=visible)
                    yield TurnFinished(
                        output=runtime_event.result.output,
                        context_meter=prepared.context_meter,
                        usage=usage,
                    )
                    return
        except asyncio.CancelledError:
            raise
        except (AIRuntimeError, MessageAuditError, ToolResultTooLargeError):
            if reasoning_open:
                yield ReasoningFinished()
            yield AssistantStreamError(code="unavailable", message="assistant model unavailable")
        except Exception:
            if reasoning_open:
                yield ReasoningFinished()
            yield AssistantStreamError(
                code="temporary_failure", message="assistant turn failed safely"
            )

    async def _persist_turn(
        self,
        conversation: Conversation,
        user_text: str,
        output: AssistantOutput,
        usage: TurnUsage,
        tool_calls: tuple[ToolCallSummary, ...],
        started_at: datetime,
    ) -> None:
        user_key = (
            f"turn:{conversation.conversation_id}:{hashlib.sha256(user_text.encode()).hexdigest()}"
        )
        updated = datetime.now(UTC)
        await self._conversations.append_turn(
            conversation.model_copy(update={"updated_at": updated}),
            self._message(ConversationRole.USER, user_text, user_key, started_at),
            self._message(
                ConversationRole.ASSISTANT,
                output.model_dump_json(),
                user_key + ":assistant",
                updated,
                usage=usage,
                tool_calls=tool_calls,
            ),
        )

    @staticmethod
    def _friendly_tool_name(tool_name: str) -> str:
        return tool_name.replace("_", " ").strip().title() or "Tool"

    async def conversation(self, conversation_id: str, device_id: str) -> Conversation:
        conversation = await self._conversations.get_conversation(
            conversation_id, self._scope(device_id)
        )
        if conversation is None:
            from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode

            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "conversation not found")
        return conversation

    async def messages(
        self, conversation_id: str, device_id: str, limit: int | None
    ) -> tuple[ConversationMessage, ...]:
        await self.conversation(conversation_id, device_id)
        if limit is None:
            return await self._conversations.all_messages(conversation_id)
        return await self._conversations.messages(conversation_id, limit=limit)

    @staticmethod
    def _message(
        role: ConversationRole,
        content: str,
        idempotency_key: str,
        created_at: datetime,
        *,
        usage: TurnUsage | None = None,
        tool_calls: tuple[ToolCallSummary, ...] = (),
    ) -> ConversationMessage:
        identity = hashlib.sha256(f"{role.value}:{idempotency_key}".encode()).hexdigest()[:32]
        return ConversationMessage(
            message_id=f"msg_{identity}",
            role=role,
            content=content,
            created_at=created_at,
            idempotency_key=idempotency_key[:200],
            usage=usage,
            tool_calls=tool_calls,
        )
