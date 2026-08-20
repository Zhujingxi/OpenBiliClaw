"""Bounded Assistant turn execution through the AI Runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openbiliclaw.ai.runtime.execution import AgentRunRequest, AgentRunResult, AIRuntime
from openbiliclaw.ai.runtime.history import ContextProjection

from .agent import (
    ASSISTANT_AGENT_ID,
    ASSISTANT_INSTRUCTIONS,
    ASSISTANT_POLICY,
    ASSISTANT_REQUIREMENTS,
)
from .history import estimate_tokens, select_history

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from .dependencies import AssistantDependencies
    from .models import AssistantOutput, ContextMeter, ConversationMessage


@dataclass(frozen=True, slots=True)
class TurnCommand:
    text: str
    deps: AssistantDependencies


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    request: AgentRunRequest[AssistantDependencies, AssistantOutput]
    context_meter: ContextMeter


@dataclass(frozen=True, slots=True)
class AssistantTurnRun:
    result: AgentRunResult[AssistantOutput]
    context_meter: ContextMeter


def turn_context(command: TurnCommand) -> tuple[ContextProjection, ...]:
    """Build bounded model context with explicit response-locale requirements."""

    profile = command.deps.profile.model_dump_json()
    response_requirements = (
        f"Answer in the language indicated by locale {command.deps.locale}. "
        "When recommending content, use human-readable titles and canonical URLs. "
        "Never expose opaque internal IDs."
    )
    return (
        ContextProjection("dialogue-profile", profile, 4_096),
        ContextProjection("response-requirements", response_requirements, 512),
    )


class AssistantService:
    def __init__(
        self,
        runtime: AIRuntime,
        agent: Agent[AssistantDependencies, AssistantOutput],
    ) -> None:
        self._runtime = runtime
        self._agent = agent
        definitions = [ASSISTANT_INSTRUCTIONS, json.dumps(agent.output_json_schema())]
        definitions.extend(
            json.dumps(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.function_schema.json_schema,
                }
            )
            for tool in agent._function_toolset.tools.values()
        )
        self._definition_tokens = sum(estimate_tokens(item) for item in definitions)

    def prepare_turn(
        self,
        command: TurnCommand,
        messages: tuple[ConversationMessage, ...] = (),
    ) -> PreparedTurn:
        """Select safe complete history against the configured model window."""

        context = turn_context(command)
        policy = self._runtime.policy(ASSISTANT_AGENT_ID, ASSISTANT_POLICY)
        window = self._runtime.context_window(ASSISTANT_AGENT_ID, ASSISTANT_REQUIREMENTS)
        base_tokens = (
            self._definition_tokens
            + estimate_tokens(command.text)
            + sum(estimate_tokens(item.label) + estimate_tokens(item.text) + 4 for item in context)
        )
        selection = select_history(
            messages,
            context_window_tokens=window,
            base_input_tokens=base_tokens,
            input_tokens_limit=policy.input_tokens_limit,
        )
        return PreparedTurn(
            request=AgentRunRequest(
                agent_id=ASSISTANT_AGENT_ID,
                agent=self._agent,
                deps=command.deps,
                user_input=command.text,
                history=selection.messages,
                context=context,
                requirements=ASSISTANT_REQUIREMENTS,
                policy=policy,
                workflow="assistant.turn",
            ),
            context_meter=selection.meter,
        )

    async def run_turn(
        self,
        command: TurnCommand,
        messages: tuple[ConversationMessage, ...] = (),
    ) -> AssistantTurnRun:
        prepared = self.prepare_turn(command, messages)
        return AssistantTurnRun(
            result=await self._runtime.run(prepared.request),
            context_meter=prepared.context_meter,
        )
