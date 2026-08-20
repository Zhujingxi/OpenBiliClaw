from __future__ import annotations

import ast
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from openbiliclaw.ai.runtime.capabilities import ModelCapabilities
from openbiliclaw.ai.runtime.execution import AgentRunRequest, AIRuntime
from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable
from openbiliclaw.core.resources import ResourceBudget
from openbiliclaw.understanding.analyzers import (
    AVOIDANCE_ANALYZER,
    INSIGHT_ANALYZER,
    PREFERENCE_ANALYZER,
    TOPIC_LIFECYCLE_ANALYZER,
)
from openbiliclaw.understanding.analyzers.preference import (
    PreferenceDraft,
    PreferenceDraftBatch,
    adapt_preference_drafts,
)
from openbiliclaw.understanding.evidence import EvidenceLink
from openbiliclaw.understanding.profile import (
    AvoidanceClaim,
    CanonicalProfile,
    PreferenceClaim,
    PreferenceDimension,
    StableInterestClaim,
    claim_id,
)
from openbiliclaw.understanding.projections import (
    DialogueProfile,
    DiscoveryProfile,
    RecommendationProfile,
    dialogue_projection,
    discovery_projection,
    recommendation_projection,
)
from openbiliclaw.understanding.proposals import ProposalBatch

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def claim(kind: str, value: str, confidence: float = 0.8) -> StableInterestClaim | AvoidanceClaim:
    cls = StableInterestClaim if kind == "stable_interest" else AvoidanceClaim
    return cls(
        claim_id=claim_id(kind, value),
        value=value,
        confidence=confidence,
        fresh_at=NOW,
        evidence_ids=("ev_" + "1" * 32,),
    )


def profiles() -> tuple[CanonicalProfile, ...]:
    sparse = CanonicalProfile.empty("sparse", NOW)
    young = CanonicalProfile(
        profile_id="young",
        revision=1,
        updated_at=NOW,
        claims=(claim("stable_interest", "science", 0.6),),
    )
    mature = CanonicalProfile(
        profile_id="mature",
        revision=8,
        updated_at=NOW,
        claims=(
            claim("stable_interest", "science", 0.95),
            claim("stable_interest", "programming", 0.9),
            PreferenceClaim(
                claim_id=claim_id("preference", "language:en"),
                dimension=PreferenceDimension.LANGUAGE,
                value="en",
                confidence=0.85,
                fresh_at=NOW,
                evidence_ids=("ev_" + "2" * 32,),
            ),
        ),
    )
    contradictory = CanonicalProfile(
        profile_id="contradictory",
        revision=2,
        updated_at=NOW,
        claims=(claim("stable_interest", "spoilers"), claim("avoidance", "spoilers")),
    )
    override_heavy = mature.model_copy(
        update={
            "profile_id": "overrides",
            "claims": tuple(
                claims for claims in mature.claims if getattr(claims, "value", "") != "science"
            ),
        }
    )
    return sparse, young, mature, contradictory, override_heavy


@pytest.mark.parametrize("profile", profiles())
def test_golden_bounded_projections(profile: CanonicalProfile) -> None:
    discovery = discovery_projection(profile, max_chars=300)
    recommendation = recommendation_projection(profile, max_chars=400)
    dialogue = dialogue_projection(profile, max_chars=500)
    assert isinstance(discovery, DiscoveryProfile)
    assert isinstance(recommendation, RecommendationProfile)
    assert isinstance(dialogue, DialogueProfile)
    assert discovery.version == dialogue.version == 1
    assert recommendation.version == 2
    assert len(discovery.model_dump_json()) <= 700
    assert len(recommendation.model_dump_json()) <= 900
    assert len(dialogue.model_dump_json()) <= 1100
    assert "evidence_ids" not in dialogue.model_dump_json()


def test_projection_hard_character_budget_and_determinism() -> None:
    many = CanonicalProfile(
        profile_id="many",
        revision=1,
        updated_at=NOW,
        claims=tuple(claim("stable_interest", f"topic-{index}") for index in range(30)),
    )
    first = discovery_projection(many, max_chars=80)
    second = discovery_projection(many, max_chars=80)
    assert first == second
    assert sum(len(item) for item in first.interests) <= 80


def test_analyzer_definitions_have_stable_typed_bounded_contracts() -> None:
    definitions = (
        PREFERENCE_ANALYZER,
        AVOIDANCE_ANALYZER,
        TOPIC_LIFECYCLE_ANALYZER,
        INSIGHT_ANALYZER,
    )
    assert len({item.agent_id.value for item in definitions}) == 4
    for item in definitions:
        assert item.context_version == 1
    assert not PREFERENCE_ANALYZER.requirements.structured_output
    assert all(item.requirements.structured_output for item in definitions[1:])
    assert PREFERENCE_ANALYZER.policy.timeout_seconds == 120
    assert all(item.policy.timeout_seconds == 30 for item in definitions[1:])
    preference_output = PREFERENCE_ANALYZER.agent.output_type
    assert isinstance(preference_output, PromptedOutput)
    assert preference_output.outputs is PreferenceDraftBatch


async def test_preference_budget_allows_bounded_prompted_output_with_reasoning_usage() -> None:
    def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart('{"proposals":[]}')],
            usage=RequestUsage(input_tokens=100, output_tokens=1_500),
        )

    model = ConfiguredModel(
        "thinking-model",
        "test",
        FunctionModel(response),
        ModelCapabilities(context_tokens=4_096),
    )
    runtime = AIRuntime(
        RouteTable(
            (
                ModelRoute(
                    PREFERENCE_ANALYZER.agent_id,
                    PREFERENCE_ANALYZER.requirements,
                    (model,),
                ),
            )
        ),
        ResourceBudget("model", 1),
    )

    result = await runtime.run(
        AgentRunRequest(
            agent_id=PREFERENCE_ANALYZER.agent_id,
            agent=PREFERENCE_ANALYZER.agent,
            deps=None,
            user_input="Evidence batch contains no supported preference signal",
            history=(),
            context=(),
            requirements=PREFERENCE_ANALYZER.requirements,
            policy=PREFERENCE_ANALYZER.policy,
            workflow="understanding.preference",
        )
    )

    assert result.output == PreferenceDraftBatch(proposals=())
    assert PREFERENCE_ANALYZER.policy.output_tokens_limit == 2_048
    assert PREFERENCE_ANALYZER.policy.total_tokens_limit == 6_144
    assert PREFERENCE_ANALYZER.policy.input_tokens_limit == 4_096
    assert PREFERENCE_ANALYZER.policy.tool_calls_limit == 1
    assert PREFERENCE_ANALYZER.policy.timeout_seconds == 120


async def test_structured_analyzer_runs_with_function_model() -> None:
    def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart('{"proposals":[]}')])

    agent: Agent[None, PreferenceDraftBatch] = Agent(
        FunctionModel(response),
        output_type=PromptedOutput(PreferenceDraftBatch),
        instructions=PREFERENCE_ANALYZER.instructions,
    )
    result = await agent.run("Evidence batch contains no supported preference signal")
    assert result.output == PreferenceDraftBatch(proposals=())


async def test_prompted_analyzer_rejects_invalid_json() -> None:
    def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("not json")])

    agent: Agent[None, PreferenceDraftBatch] = Agent(
        FunctionModel(response), output_type=PromptedOutput(PreferenceDraftBatch)
    )
    with pytest.raises(UnexpectedModelBehavior, match="output validation"):
        await agent.run("Return an empty proposal batch")


def test_preference_drafts_gain_deterministic_domain_identity_and_resolve_evidence() -> None:
    evidence = EvidenceLink(
        evidence_id="ev_" + "a" * 32,
        observation_id="obs_" + "a" * 32,
        summary="Preference statement: practical Python",
        occurred_at=NOW,
        trust=1,
    )
    batch = PreferenceDraftBatch(
        proposals=(
            PreferenceDraft(
                dimension="content",
                value="practical Python",
                confidence=0.9,
                evidence_ids=(evidence.evidence_id,),
            ),
        )
    )
    adapted = adapt_preference_drafts(batch, (evidence,), PREFERENCE_ANALYZER.agent_id.value, NOW)
    assert ProposalBatch.model_validate_json(adapted.model_dump_json()) == adapted
    assert adapted.proposals[0].claim.claim_id == claim_id("preference", "content:practical Python")
    hallucinated = batch.model_copy(
        update={
            "proposals": (batch.proposals[0].model_copy(update={"evidence_ids": ("invented",)}),)
        }
    )
    assert not adapt_preference_drafts(
        hallucinated, (evidence,), PREFERENCE_ANALYZER.agent_id.value, NOW
    ).proposals


def _imports(node: ast.AST, package: str) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        if node.level:
            base = package.split(".")[: -(node.level - 1)] if node.level > 1 else package.split(".")
            return (".".join((*base, node.module)),)
        return (node.module,)
    return ()


def test_understanding_imports_only_approved_boundaries() -> None:
    root = Path(__file__).parents[2] / "src" / "openbiliclaw" / "understanding"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        package = "openbiliclaw.understanding" + "." + ".".join(path.relative_to(root).parent.parts)
        for node in ast.walk(ast.parse(path.read_text())):
            for module in _imports(node, package.rstrip(".")):
                top = module.split(".")[0]
                allowed = (
                    top in sys.stdlib_module_names
                    or top in {"pydantic", "pydantic_ai"}
                    or module.startswith("openbiliclaw.understanding")
                    or module.startswith("openbiliclaw.observations")
                    or module.startswith("openbiliclaw.ai")
                    or module.startswith("openbiliclaw.core")
                    or module.startswith("openbiliclaw.infrastructure")
                )
                if not allowed:
                    violations.append(f"{path.name}:{getattr(node, 'lineno', 0)}:{module}")
    assert not violations


def test_product_consumers_do_not_import_canonical_profile() -> None:
    source = Path(__file__).parents[2] / "src" / "openbiliclaw"
    forbidden_roots = ("discovery", "recommendation", "assistant", "hosts")
    offenders: list[str] = []
    for root_name in forbidden_roots:
        root = source / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "openbiliclaw.understanding.profile" in path.read_text():
                offenders.append(str(path.relative_to(source)))
    assert not offenders
