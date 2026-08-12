from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic_ai.models.test import TestModel

from openbiliclaw.ai.runtime.capabilities import ModelCapabilities
from openbiliclaw.ai.runtime.execution import AIRuntime
from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable
from openbiliclaw.assistant.agent import (
    ASSISTANT_AGENT_ID,
    ASSISTANT_REQUIREMENTS,
    build_assistant_agent,
)
from openbiliclaw.assistant.service import AssistantService
from openbiliclaw.composition.assistant import (
    AssistantController,
    _AssistantToolFacade,
    assistant_workflow_tools,
)
from openbiliclaw.composition.build import BuildOptions, build_application
from openbiliclaw.core.config import AppSettings
from openbiliclaw.core.resources import ResourceBudget
from openbiliclaw.hosts.api.schemas.models import AssistantTurnRequest

if TYPE_CHECKING:
    from pathlib import Path


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
async def test_assistant_tool_facade_delegates_reads_and_blocks_mutations(tmp_path: Path) -> None:
    application = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    assert application.services.facade is not None
    facade = _AssistantToolFacade(application.services.facade)
    await application.start()
    try:
        assert await facade.get_recommendations(1) is not None
        assert await facade.show_profile() is not None
        assert await facade.list_sources() is not None
        for operation in (
            facade.record_feedback("ref", "liked"),
            facade.edit_profile("claim", "set", "x"),
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
        "edit_profile",
        "list_sources",
        "connect_source",
        "record_feedback",
    }
