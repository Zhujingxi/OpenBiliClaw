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
from openbiliclaw.composition.assistant import AssistantController
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
