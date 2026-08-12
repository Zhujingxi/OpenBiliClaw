"""Typed optional expression agent contract."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ConfigDict, Field
from pydantic_ai import Agent

from openbiliclaw.ai.runtime.budgets import RunPolicy
from openbiliclaw.ai.runtime.capabilities import AgentId, ModelRequirements
from openbiliclaw.core._pydantic import StrictBaseModel


class ExpressedItem(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recommendation_id: str
    reason: str = Field(min_length=1, max_length=300)
    tone: str = Field(min_length=1, max_length=60)


class ExpressionBatch(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[ExpressedItem, ...] = Field(max_length=20)


@dataclass(frozen=True, slots=True)
class ExpressionAgentDefinition:
    agent_id: AgentId
    agent: Agent[None, ExpressionBatch]
    requirements: ModelRequirements
    policy: RunPolicy


EXPRESSION_AGENT = ExpressionAgentDefinition(
    AgentId("recommendation.expression"),
    Agent(
        output_type=ExpressionBatch,
        instructions="Write concise reasons only. Preserve recommendation IDs and order.",
    ),
    ModelRequirements(structured_output=True, context_tokens=4096),
    RunPolicy(
        request_limit=2,
        input_tokens_limit=4096,
        output_tokens_limit=2048,
        total_tokens_limit=6144,
        tool_calls_limit=1,
        timeout_seconds=30,
        retries=0,
    ),
)
