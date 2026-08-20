"""PydanticAI analyzer definitions with stable identities and bounded runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic_ai import Agent, PromptedOutput

from openbiliclaw.ai.runtime.budgets import RunPolicy
from openbiliclaw.ai.runtime.capabilities import AgentId, ModelRequirements

from ..proposals import ProposalBatch
from .preference import PreferenceDraftBatch

OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class AnalyzerDefinition(Generic[OutputT]):
    agent_id: AgentId
    instructions: str
    agent: Agent[None, OutputT]
    requirements: ModelRequirements
    policy: RunPolicy
    context_version: int = 1


def _definition(name: str, instructions: str) -> AnalyzerDefinition[ProposalBatch]:
    requirements = ModelRequirements(structured_output=True, context_tokens=4_096)
    return AnalyzerDefinition(
        agent_id=AgentId(f"understanding.{name}.v1"),
        instructions=instructions,
        agent=Agent(output_type=ProposalBatch, instructions=instructions),
        requirements=requirements,
        policy=RunPolicy(
            request_limit=2,
            input_tokens_limit=4_096,
            output_tokens_limit=2_048,
            total_tokens_limit=6_144,
            tool_calls_limit=1,
            timeout_seconds=30,
            retries=0,
        ),
    )


PREFERENCE_ANALYZER = AnalyzerDefinition(
    agent_id=AgentId("understanding.preference.v1"),
    instructions=(
        "Infer only evidenced content, style, creator, language, or provider preferences. "
        "Copy evidence_id values exactly from the supplied evidence."
    ),
    agent=Agent(output_type=PromptedOutput(PreferenceDraftBatch)),
    requirements=ModelRequirements(context_tokens=4_096),
    policy=RunPolicy(
        request_limit=2,
        input_tokens_limit=4_096,
        output_tokens_limit=2_048,
        total_tokens_limit=6_144,
        tool_calls_limit=1,
        timeout_seconds=120,
        retries=0,
    ),
)
AVOIDANCE_ANALYZER = _definition(
    "avoidance", "Infer only evidenced avoidances; never infer sensitive traits."
)
TOPIC_LIFECYCLE_ANALYZER = _definition(
    "topic_lifecycle", "Classify evidenced topics as emerging, active, or retired."
)
INSIGHT_ANALYZER = _definition(
    "insight", "Propose short higher-level insight statements grounded in supplied evidence."
)
