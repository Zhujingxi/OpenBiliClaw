from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from pydantic_ai import Tool
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, FunctionModel
from pydantic_ai.models.test import TestModel

from openbiliclaw.ai.runtime.capabilities import ModelCapabilities
from openbiliclaw.ai.runtime.errors import UnavailableError
from openbiliclaw.ai.runtime.execution import AIRuntime
from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable
from openbiliclaw.application.content_actions import ConfirmContentActionCommand
from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.assistant.agent import (
    ASSISTANT_AGENT_ID,
    ASSISTANT_REQUIREMENTS,
    build_assistant_agent,
)
from openbiliclaw.assistant.models import (
    AssistantStreamError,
    ReasoningStarted,
    ToolFinished,
    ToolStarted,
    TurnFinished,
)
from openbiliclaw.assistant.service import AssistantService
from openbiliclaw.composition.assistant import (
    AssistantController,
    _AssistantToolFacade,
    assistant_recommendation_context,
    assistant_workflow_tools,
)
from openbiliclaw.composition.build import BuildOptions, build_application
from openbiliclaw.core.config import AppSettings
from openbiliclaw.core.resources import ResourceBudget
from openbiliclaw.hosts.api.schemas.models import AssistantTurnRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from pydantic_ai.messages import ModelMessage

    from openbiliclaw.application.reads import RecommendationsResult
    from openbiliclaw.assistant.models import AssistantLifecycleEvent, ConversationMessage


def test_assistant_recommendation_context_exposes_titles_and_links_not_internal_ids() -> None:
    result = cast(
        "RecommendationsResult",
        SimpleNamespace(
            items=(
                SimpleNamespace(
                    shown_id="shown_" + "1" * 32,
                    ref=SimpleNamespace(canonical_url="https://example.com/watch/1"),
                    card=SimpleNamespace(title="Readable title", summary="Readable summary"),
                    reason="Matches your interests",
                ),
            )
        ),
    )
    context = assistant_recommendation_context(result)
    assert context == {
        "items": (
            {
                "title": "Readable title",
                "canonical_url": "https://example.com/watch/1",
                "summary": "Readable summary",
                "reason": "Matches your interests",
            },
        )
    }
    assert "shown_" not in str(context)


@pytest.mark.asyncio
async def test_controller_persists_scoped_turn_and_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    await application.start()
    assert application.repositories is not None
    assert application.services.understanding is not None
    assert application.services.facade is not None
    model = TestModel(custom_output_args={"kind": "message", "text": "hello"})
    runtime = AIRuntime(
        RouteTable(
            (
                ModelRoute(
                    ASSISTANT_AGENT_ID,
                    ASSISTANT_REQUIREMENTS,
                    (
                        ConfiguredModel(
                            "assistant-test",
                            "test",
                            model,
                            ModelCapabilities(
                                tools=True,
                                structured_output=True,
                                context_tokens=16_000,
                                streaming=True,
                            ),
                        ),
                    ),
                ),
            )
        ),
        ResourceBudget("assistant", 1),
    )
    controller = AssistantController(
        AssistantService(runtime, build_assistant_agent()),
        application.repositories.conversations,
        application.services.understanding,
        application.services.facade,
    )
    conversation_id = "conv_" + "a" * 32
    request = AssistantTurnRequest(conversation_id=conversation_id, text="hi", locale="en-US")
    output = await controller.turn(request, "desktop")
    assert output.kind == "message"
    await controller.turn(request, "desktop")
    assert len(await controller.messages(conversation_id, "desktop", 20)) == 4
    retry = AssistantTurnRequest(
        conversation_id=conversation_id,
        text="hi",
        locale="en-US",
        turn_key="client-retry-0001",
    )
    await controller.turn(retry, "desktop")
    await controller.turn(retry, "desktop")

    owner_conversation = await controller.conversation(conversation_id, "desktop")
    assert owner_conversation.updated_at <= datetime.now(UTC)
    messages = await controller.messages(conversation_id, "desktop", 20)
    assert tuple(message.role.value for message in messages) == (
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    )

    history_reads = 0
    original_all_messages = application.repositories.conversations.all_messages

    async def tracked_all_messages(
        target_conversation_id: str,
    ) -> tuple[ConversationMessage, ...]:
        nonlocal history_reads
        history_reads += 1
        return await original_all_messages(target_conversation_id)

    monkeypatch.setattr(
        application.repositories.conversations, "all_messages", tracked_all_messages
    )
    with pytest.raises(ApplicationError) as caught:
        await controller.turn(request, "other-device")
    assert caught.value.code is ApplicationErrorCode.NOT_FOUND
    assert history_reads == 0
    assert (
        await application.repositories.conversations.get_conversation_unscoped(conversation_id)
        == owner_conversation
    )
    assert await application.repositories.conversations.all_messages(conversation_id) == messages

    monkeypatch.setattr(
        "openbiliclaw.composition.assistant.render_assistant_output",
        lambda output: "cookie: never-visible",
    )
    rejected_id = "conv_" + "d" * 32
    rejected_events = [
        event
        async for event in controller.stream_turn(
            AssistantTurnRequest(conversation_id=rejected_id, text="audit me", locale="en-US"),
            "desktop",
        )
    ]
    assert isinstance(rejected_events[-1], AssistantStreamError)
    assert (
        await application.repositories.conversations.get_conversation_unscoped(rejected_id) is None
    )
    assert await application.repositories.conversations.all_messages(rejected_id) == ()
    await application.stop()


@pytest.mark.asyncio
async def test_controller_streams_safe_tools_and_persists_only_summaries(tmp_path: Path) -> None:
    application = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    await application.start()
    assert application.repositories is not None
    assert application.services.understanding is not None
    assert application.services.facade is not None

    async def search_content() -> str:
        return "private native payload"

    model = TestModel(
        call_tools="all", custom_output_args={"kind": "message", "text": "Safe answer"}
    )
    runtime = AIRuntime(
        RouteTable(
            (
                ModelRoute(
                    ASSISTANT_AGENT_ID,
                    ASSISTANT_REQUIREMENTS,
                    (
                        ConfiguredModel(
                            "assistant-test",
                            "test",
                            model,
                            ModelCapabilities(
                                tools=True,
                                structured_output=True,
                                context_tokens=16_000,
                                streaming=True,
                            ),
                        ),
                    ),
                ),
            )
        ),
        ResourceBudget("assistant", 1),
    )
    controller = AssistantController(
        AssistantService(
            runtime,
            build_assistant_agent((Tool(search_content, name="search_content"),)),
        ),
        application.repositories.conversations,
        application.services.understanding,
        application.services.facade,
    )
    conversation_id = "conv_" + "c" * 32
    events = [
        event
        async for event in controller.stream_turn(
            AssistantTurnRequest(
                conversation_id=conversation_id, text="find something", locale="en-US"
            ),
            "desktop",
        )
    ]

    assert any(isinstance(event, ToolStarted) for event in events)
    assert any(isinstance(event, ToolFinished) for event in events)
    assert isinstance(events[-1], TurnFinished)
    assert "private native payload" not in repr(events)
    messages = await controller.messages(conversation_id, "desktop", 20)
    assert messages[-1].tool_calls[0].tool_name == "Search Content"
    assert messages[-1].tool_calls[0].safe_summary == "Search Content completed."
    assert "private native payload" not in messages[-1].model_dump_json()
    await application.stop()


@pytest.mark.asyncio
async def test_controller_close_releases_complete_runtime_stream_chain(tmp_path: Path) -> None:
    application = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    await application.start()
    assert application.repositories is not None
    assert application.services.understanding is not None
    assert application.services.facade is not None

    async def stream_response(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[dict[int, DeltaThinkingPart]]:
        del messages, info
        yield {0: DeltaThinkingPart(content="thinking")}
        await asyncio.Event().wait()

    configured = ConfiguredModel(
        "assistant-test",
        "test",
        FunctionModel(stream_function=stream_response),
        ModelCapabilities(
            tools=True,
            structured_output=True,
            context_tokens=16_000,
            streaming=True,
        ),
    )
    runtime = AIRuntime(
        RouteTable((ModelRoute(ASSISTANT_AGENT_ID, ASSISTANT_REQUIREMENTS, (configured,)),)),
        ResourceBudget("assistant", 1),
    )
    controller = AssistantController(
        AssistantService(runtime, build_assistant_agent()),
        application.repositories.conversations,
        application.services.understanding,
        application.services.facade,
    )
    stream = controller.stream_turn(
        AssistantTurnRequest(
            conversation_id="conv_" + "f" * 32,
            text="think",
            locale="en-US",
        ),
        "desktop",
    )
    try:
        assert (await anext(stream)).kind == "turn_started"
        assert isinstance(await anext(stream), ReasoningStarted)
        assert runtime.active_runs == 1
        await stream.aclose()
        assert runtime.active_runs == 0
    finally:
        await application.stop()


@pytest.mark.asyncio
async def test_controller_translates_ai_runtime_failure_to_typed_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    await application.start()
    assert application.repositories is not None
    assert application.services.understanding is not None
    assert application.services.facade is not None
    runtime = AIRuntime(
        RouteTable(
            (
                ModelRoute(
                    ASSISTANT_AGENT_ID,
                    ASSISTANT_REQUIREMENTS,
                    (
                        ConfiguredModel(
                            "assistant-test",
                            "test",
                            TestModel(),
                            ModelCapabilities(
                                tools=True,
                                structured_output=True,
                                context_tokens=16_000,
                                streaming=True,
                            ),
                        ),
                    ),
                ),
            )
        ),
        ResourceBudget("assistant", 1),
    )
    service = AssistantService(runtime, build_assistant_agent())
    controller = AssistantController(
        service,
        application.repositories.conversations,
        application.services.understanding,
        application.services.facade,
    )

    def failing_prepare(command: object, messages: object) -> object:
        del command, messages
        raise ValueError("projection is oversized")

    prepare_failure_id = "conv_" + "e" * 32
    with monkeypatch.context() as patch:
        patch.setattr(service, "prepare_turn", failing_prepare)
        events = [
            event
            async for event in controller.stream_turn(
                AssistantTurnRequest(conversation_id=prepare_failure_id, text="hi", locale="en-US"),
                "desktop",
            )
        ]
    assert len(events) == 1
    assert isinstance(events[0], AssistantStreamError)
    assert (
        await application.repositories.conversations.get_conversation_unscoped(prepare_failure_id)
        is None
    )

    async def failing_stream(prepared: object) -> AsyncIterator[AssistantLifecycleEvent]:
        del prepared
        raise UnavailableError(model_instance="test:model")
        yield  # pragma: no cover

    monkeypatch.setattr(service, "stream_turn", failing_stream)
    with pytest.raises(ApplicationError) as caught:
        await controller.turn(
            AssistantTurnRequest(conversation_id="conv_" + "b" * 32, text="hi", locale="en-US"),
            "desktop",
        )
    assert caught.value.code is ApplicationErrorCode.UNAVAILABLE
    await application.stop()


@pytest.mark.asyncio
async def test_assistant_tool_facade_proposes_but_never_applies_profile_mutations(
    tmp_path: Path,
) -> None:
    application = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    assert application.services.facade is not None
    assert application.services.understanding is not None
    facade = _AssistantToolFacade(application.services.facade)
    await application.start()
    try:
        assert await facade.get_recommendations(1) is not None
        assert await facade.show_profile() is not None
        assert await facade.list_sources() is not None
        before = await application.services.facade.show_profile("default")
        pending = await facade.propose_profile_revision(
            "exploration.disabled", "set", "true", "stop exploring"
        )
        after = await application.services.facade.show_profile("default")
        assert pending.kind == "profile_revision"
        duplicate = await facade.propose_profile_revision(
            "exploration.disabled", "set", "true", "same correction, retried"
        )
        assert duplicate.pending_action_id == pending.pending_action_id
        assert after == before
        assert not hasattr(facade, "edit_profile")
        await application.services.facade.confirm_action(
            ConfirmContentActionCommand(
                pending_action_id=pending.pending_action_id, user_id="local"
            )
        )
        canonical = await application.services.understanding.profile("default")
        # RecommendationPipeline's B5 branch compiles this explicit flag to
        # exploit-only allocation; passive behavior cannot produce the override.
        assert canonical.exploration_disabled()
        assert not canonical.claims
        for operation in (
            facade.record_feedback("ref", "liked"),
            facade.connect_source("demo"),
        ):
            with pytest.raises(RuntimeError, match="require"):
                await operation
    finally:
        await application.stop()


def test_composition_attaches_safe_application_workflow_tools(tmp_path: Path) -> None:
    application = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    assert application.services.facade is not None
    agent = build_assistant_agent(assistant_workflow_tools(application.services.facade))
    assert set(agent._function_toolset.tools) == {
        "get_recommendations",
        "get_content_details",
        "search_content",
        "show_profile",
        "propose_profile_revision",
        "list_sources",
        "connect_source",
        "record_feedback",
    }
