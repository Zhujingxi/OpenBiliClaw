"""Reusable PydanticAI task definitions for the retained vNext product flow."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, UsageLimits

from openbiliclaw.features.feed.domain import CandidateAssessment, ContentItem
from openbiliclaw.features.profile.domain import ProfileDelta, ProfileSnapshot
from openbiliclaw.infrastructure.ai.spec import CachePolicy, TaskLane, TaskSpec


class ProfileDeltaInput(BaseModel):
    """Evidence and current profile supplied to profile-delta generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: ProfileSnapshot
    evidence: tuple[str, ...] = Field(min_length=1)


class KeywordGenerationInput(BaseModel):
    """Current profile and desired number of source-neutral search queries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: ProfileSnapshot
    limit: int = Field(default=8, ge=1, le=30)


class KeywordGenerationOutput(BaseModel):
    """Ordered, deduplicated discovery queries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    keywords: tuple[str, ...] = Field(min_length=1, max_length=30)


class CandidateAssessmentInput(BaseModel):
    """Profile-relative content assessment input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: ProfileSnapshot
    content: ContentItem


class RecommendationExplanationInput(BaseModel):
    """Facts permitted in one user-facing recommendation explanation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: ProfileSnapshot
    content: ContentItem
    assessment: CandidateAssessment


class RecommendationExplanationOutput(BaseModel):
    """Short grounded explanation rendered by application surfaces."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    explanation: str = Field(min_length=1, max_length=1000)


PROFILE_DELTA_AGENT: Agent[None, ProfileDelta] = Agent(
    output_type=ProfileDelta,
    instructions="Propose only evidence-grounded changes to the revisioned user profile.",
)
KEYWORD_GENERATION_AGENT: Agent[None, KeywordGenerationOutput] = Agent(
    output_type=KeywordGenerationOutput,
    instructions="Generate concise source-neutral discovery queries from the supplied profile.",
)
CANDIDATE_ASSESSMENT_AGENT: Agent[None, CandidateAssessment] = Agent(
    output_type=CandidateAssessment,
    instructions="Assess the supplied content only against the supplied profile evidence.",
)
RECOMMENDATION_EXPLANATION_AGENT: Agent[None, RecommendationExplanationOutput] = Agent(
    output_type=RecommendationExplanationOutput,
    instructions="Explain the recommendation using only supplied content and assessment facts.",
)

PROFILE_DELTA_TASK = TaskSpec(
    name="profile_delta",
    input_type=ProfileDeltaInput,
    output_type=ProfileDelta,
    agent=PROFILE_DELTA_AGENT,
    model_alias="obc-analysis",
    semantic_retry_limit=2,
    timeout_seconds=90,
    usage_limits=UsageLimits(request_limit=3, total_tokens_limit=12_000),
    cache_policy=CachePolicy.DEFAULT,
    lane=TaskLane.ANALYSIS,
)
KEYWORD_GENERATION_TASK = TaskSpec(
    name="keyword_generation",
    input_type=KeywordGenerationInput,
    output_type=KeywordGenerationOutput,
    agent=KEYWORD_GENERATION_AGENT,
    model_alias="obc-analysis",
    semantic_retry_limit=2,
    timeout_seconds=60,
    usage_limits=UsageLimits(request_limit=3, total_tokens_limit=8_000),
    cache_policy=CachePolicy.DEFAULT,
    lane=TaskLane.ANALYSIS,
)
CANDIDATE_ASSESSMENT_TASK = TaskSpec(
    name="candidate_assessment",
    input_type=CandidateAssessmentInput,
    output_type=CandidateAssessment,
    agent=CANDIDATE_ASSESSMENT_AGENT,
    model_alias="obc-analysis",
    semantic_retry_limit=2,
    timeout_seconds=60,
    usage_limits=UsageLimits(request_limit=3, total_tokens_limit=8_000),
    cache_policy=CachePolicy.DEFAULT,
    lane=TaskLane.ANALYSIS,
)
RECOMMENDATION_EXPLANATION_TASK = TaskSpec(
    name="recommendation_explanation",
    input_type=RecommendationExplanationInput,
    output_type=RecommendationExplanationOutput,
    agent=RECOMMENDATION_EXPLANATION_AGENT,
    model_alias="obc-interactive",
    semantic_retry_limit=1,
    timeout_seconds=30,
    usage_limits=UsageLimits(request_limit=2, total_tokens_limit=4_000),
    cache_policy=CachePolicy.DEFAULT,
    lane=TaskLane.INTERACTIVE,
)
