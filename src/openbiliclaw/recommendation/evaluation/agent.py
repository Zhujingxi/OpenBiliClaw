"""One-shot typed candidate evaluation contract."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ConfigDict, Field, model_validator
from pydantic_ai import Agent

from openbiliclaw.ai.runtime.budgets import RunPolicy
from openbiliclaw.ai.runtime.capabilities import AgentId, ModelRequirements
from openbiliclaw.content.integration.projections import (
    ContentPreview,  # noqa: TC001  # Runtime type required by Pydantic model fields.
)
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.understanding.projections import (
    RecommendationProfile,  # noqa: TC001  # Runtime type required by Pydantic model fields.
)


class CandidateScore(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str
    score: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=500)
    uncertainty: float = Field(ge=0, le=1)


class EvaluationBatch(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    results: tuple[CandidateScore, ...] = Field(max_length=20)

    @model_validator(mode="after")
    def unique_ids(self) -> EvaluationBatch:
        ids = tuple(x.candidate_id for x in self.results)
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate candidate evaluation")
        return self


class EvaluationInput(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile: RecommendationProfile
    candidates: tuple[ContentPreview, ...] = Field(max_length=20)
    candidate_ids: tuple[str, ...] = Field(max_length=20)
    rubric_version: int = 1

    @model_validator(mode="after")
    def aligned(self) -> EvaluationInput:
        if len(self.candidates) != len(self.candidate_ids):
            raise ValueError("candidate IDs and previews must align")
        return self


@dataclass(frozen=True, slots=True)
class EvaluationAgentDefinition:
    agent_id: AgentId
    agent: Agent[None, EvaluationBatch]
    requirements: ModelRequirements
    policy: RunPolicy
    rubric_version: int = 1
    context_version: int = 1


EVALUATION_AGENT = EvaluationAgentDefinition(
    AgentId("recommendation.evaluate"),
    Agent(
        output_type=EvaluationBatch,
        instructions=(
            "Score every candidate once using relevance, novelty, quality and risk. "
            "Return no missing or duplicate IDs."
        ),
    ),
    ModelRequirements(structured_output=True, context_tokens=8192),
    RunPolicy(
        request_limit=2,
        input_tokens_limit=8192,
        output_tokens_limit=4096,
        total_tokens_limit=12288,
        tool_calls_limit=1,
        timeout_seconds=45,
        retries=0,
    ),
)


def validate_complete(output: EvaluationBatch, expected: tuple[str, ...]) -> None:
    if set(x.candidate_id for x in output.results) != set(expected) or len(output.results) != len(
        expected
    ):
        raise ValueError("evaluation must contain exactly one result per candidate")
