"""Typed provider-neutral recommendation query agent."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ConfigDict, Field
from pydantic_ai import Agent

from openbiliclaw.ai.runtime.budgets import RunPolicy
from openbiliclaw.ai.runtime.capabilities import AgentId, ModelRequirements
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.understanding.projections import (
    DiscoveryProfile,  # noqa: TC001  # Runtime type required by Pydantic model fields.
)


class QuerySuggestion(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=80)


class QueryBatch(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    suggestions: tuple[QuerySuggestion, ...] = Field(max_length=10)


class QueryInput(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile: DiscoveryProfile
    inventory_pressure: float = Field(ge=0, le=1)


@dataclass(frozen=True, slots=True)
class QueryAgentDefinition:
    agent_id: AgentId
    agent: Agent[None, QueryBatch]
    requirements: ModelRequirements
    policy: RunPolicy
    context_version: int = 1


QUERY_AGENT = QueryAgentDefinition(
    AgentId("recommendation.query"),
    Agent(
        output_type=QueryBatch,
        instructions=(
            "Generate concise provider-neutral discovery queries. "
            "Never include credentials or provider syntax."
        ),
    ),
    ModelRequirements(structured_output=True, context_tokens=4096),
    RunPolicy(
        request_limit=2,
        input_tokens_limit=4096,
        output_tokens_limit=1024,
        total_tokens_limit=5120,
        tool_calls_limit=1,
        timeout_seconds=30,
        retries=0,
    ),
)
