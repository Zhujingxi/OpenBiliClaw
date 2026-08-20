from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
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
    from pathlib import Path

    from openbiliclaw.application.reads import RecommendationsResult


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
async def test_controller_persists_scoped_turn_and_messages(tmp_path: Path) -> None:
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
    output = await controller.turn(
        AssistantTurnRequest(conversation_id=conversation_id, text="hi", locale="en-US"),
        "desktop",
    )
    assert output.kind == "message"
    assert (await controller.conversation(conversation_id, "desktop")).updated_at <= datetime.now(
        UTC
    )
    messages = await controller.messages(conversation_id, "desktop", 20)
    assert tuple(message.role.value for message in messages) == ("user", "assistant")
    with pytest.raises(Exception, match="not found"):
        await controller.conversation(conversation_id, "other-device")
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
                            ),
                        ),
                    ),
                ),
            )
        ),
        ResourceBudget("assistant", 1),
    )
    service = AssistantService(runtime, build_assistant_agent())

    async def failing_turn(command: object, messages: object = ()) -> object:
        del command, messages
        raise UnavailableError(model_instance="test:model")

    monkeypatch.setattr(service, "run_turn", failing_turn)
    controller = AssistantController(
        service,
        application.repositories.conversations,
        application.services.understanding,
        application.services.facade,
    )
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
