"""Bounded Assistant turn execution through the AI Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openbiliclaw.ai.runtime.execution import AgentRunRequest, AgentRunResult, AIRuntime
from openbiliclaw.ai.runtime.history import ContextProjection

from .agent import ASSISTANT_AGENT_ID, ASSISTANT_POLICY, ASSISTANT_REQUIREMENTS

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from .dependencies import AssistantDependencies
    from .models import AssistantOutput


@dataclass(frozen=True, slots=True)
class TurnCommand:
    text: str
    deps: AssistantDependencies


class AssistantService:
    def __init__(
        self,
        runtime: AIRuntime,
        agent: Agent[AssistantDependencies, AssistantOutput],
    ) -> None:
        self._runtime = runtime
        self._agent = agent

    async def run_turn(self, command: TurnCommand) -> AgentRunResult[AssistantOutput]:
        profile = command.deps.profile.model_dump_json()
        return await self._runtime.run(
            AgentRunRequest(
                agent_id=ASSISTANT_AGENT_ID,
                agent=self._agent,
                deps=command.deps,
                user_input=command.text,
                history=(),
                context=(ContextProjection("dialogue-profile", profile, 4_096),),
                requirements=ASSISTANT_REQUIREMENTS,
                policy=ASSISTANT_POLICY,
                workflow="assistant.turn",
            )
        )
