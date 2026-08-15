"""RecommendationBrief shadow compilation and journaling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import ValidationError
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from openbiliclaw.ai.runtime.capabilities import ModelCapabilities
from openbiliclaw.ai.runtime.execution import AgentRunRequest, AIRuntime
from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable
from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    BiasClass,
    CapabilityKind,
    ChannelDescriptor,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)
from openbiliclaw.content.providers.bilibili.manifest import BILIBILI_MANIFEST
from openbiliclaw.core.resources import ResourceBudget
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.recommendation.brief import (
    BriefCompiler,
    BriefHypothesis,
    BriefIntent,
    BriefService,
    InspectionPlan,
    RecommendationBrief,
    RetrievalPlan,
    SlateGuidance,
)
from openbiliclaw.recommendation.brief_agent import BRIEF_AGENT
from openbiliclaw.recommendation.hypotheses import HypothesisRegistry
from openbiliclaw.recommendation.policy_journal import SqlitePolicyJournal

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def manifest() -> ProviderManifest:
    return ProviderManifest(
        provider_id=ProviderId(value="demo"),
        display_name="Demo",
        capabilities=frozenset({CapabilityKind.FEED, CapabilityKind.SEARCH}),
        native_schemas=(
            NativeSchemaDescriptor(content_kind=ContentKind(value="video"), schema_version=1),
        ),
        channels=(
            ChannelDescriptor(
                feed_id="popular",
                bias_class=BiasClass.PLATFORM_POPULARITY,
                auth_required=False,
            ),
            ChannelDescriptor(
                feed_id="for-you",
                bias_class=BiasClass.PLATFORM_PERSONALIZED,
                auth_required=True,
            ),
        ),
        availability=ProviderAvailability.AVAILABLE,
    )


def proposal(**updates: object) -> RecommendationBrief:
    data: dict[str, object] = {
        "intent": BriefIntent.EXPLORE,
        "intent_expires_at": NOW + timedelta(hours=1),
        "hypotheses": (
            BriefHypothesis(
                statement="Public popularity may reveal a useful adjacent topic",
                evidence_refs=("ev_1234567890abcdef1234567890abcdef",),
                falsification="three exposed items receive no meaningful engagement",
                expires_at=NOW + timedelta(hours=2),
            ),
        ),
        "retrieval_plans": (
            RetrievalPlan(
                channel_refs=("demo:popular",),
                keyword_queries=("adjacent science",),
                exploration=True,
            ),
        ),
        "inspection_plan": InspectionPlan(
            shortlist_targets=("candidate:best-adjacent",),
            quality_rubric="Prefer substantive explanations over clickbait.",
        ),
        "slate_guidance": SlateGuidance(
            familiar_relationship="Anchor novelty in known science interests.",
            novel_relationship="Bridge one adjacent topic through shared concepts.",
        ),
        "action": "recommend",
        "question": None,
        "stop_condition": "Stop after one bounded slate or clear negative feedback.",
        "expires_at": NOW + timedelta(hours=3),
    }
    data.update(updates)
    return RecommendationBrief.model_validate(data)


def runtime(model: TestModel | FunctionModel) -> AIRuntime:
    configured = ConfiguredModel(
        "brief-test",
        "test",
        model,
        ModelCapabilities(structured_output=True, context_tokens=8192),
    )
    return AIRuntime(
        RouteTable((ModelRoute(BRIEF_AGENT.agent_id, BRIEF_AGENT.requirements, (configured,)),)),
        ResourceBudget("model", 1),
    )


async def test_typed_brief_output_parses_through_agent_runtime() -> None:
    model = TestModel(custom_output_args=proposal().model_dump(mode="json"))
    service_runtime = runtime(model)

    result = await service_runtime.run(
        AgentRunRequest(
            agent_id=BRIEF_AGENT.agent_id,
            agent=BRIEF_AGENT.agent,
            deps=None,
            user_input="{}",
            history=(),
            context=(),
            requirements=BRIEF_AGENT.requirements,
            policy=BRIEF_AGENT.policy,
            workflow="recommendation.brief",
            recommendation_batch="episode-typed",
        )
    )
    assert result.output == proposal()


def test_slate_guidance_schema_rejects_percentages() -> None:
    with pytest.raises(ValidationError, match="percentage"):
        SlateGuidance(
            familiar_relationship="Keep 80% familiar.",
            novel_relationship="Use an adjacent relationship.",
        )


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (
            lambda: proposal(
                retrieval_plans=(
                    RetrievalPlan(channel_refs=("missing:popular",), exploration=True),
                )
            ),
            "unknown-channel",
        ),
        (
            lambda: proposal(
                retrieval_plans=(RetrievalPlan(channel_refs=("demo:for-you",), exploration=True),)
            ),
            "personalized-exploration",
        ),
        (lambda: proposal(action="ask", question=None), "ask-question-required"),
        (
            lambda: proposal(
                inspection_plan=InspectionPlan(
                    shortlist_targets=("one", "two", "three"), quality_rubric="quality"
                )
            ),
            "inspection-budget",
        ),
        (lambda: proposal(expires_at=NOW - timedelta(seconds=1)), "brief-expired"),
        (
            lambda: proposal(intent_expires_at=NOW - timedelta(seconds=1)),
            "intent-expired",
        ),
        (
            lambda: proposal(intent_expires_at=NOW + timedelta(days=1)),
            "intent-expiry-order",
        ),
        (
            lambda: proposal(
                hypotheses=(
                    BriefHypothesis(
                        statement="Stale hypothesis",
                        evidence_refs=("ev_1234567890abcdef1234567890abcdef",),
                        falsification="no engagement",
                        expires_at=NOW - timedelta(seconds=1),
                    ),
                )
            ),
            "hypothesis-expired",
        ),
        (
            lambda: proposal(
                hypotheses=(
                    BriefHypothesis(
                        statement="Outliving hypothesis",
                        evidence_refs=("ev_1234567890abcdef1234567890abcdef",),
                        falsification="no engagement",
                        expires_at=NOW + timedelta(days=1),
                    ),
                )
            ),
            "hypothesis-expiry-order",
        ),
        (lambda: proposal(action="recommend", question="why not?"), "unexpected-question"),
    ],
)
def test_compiler_rejects_invalid_or_unsafe_plans(candidate: Any, reason: str) -> None:
    compiled = BriefCompiler((manifest(),), inspection_shortlist_cap=2).compile(
        candidate(), now=NOW
    )

    assert not compiled.accepted
    assert reason in {diagnostic.code for diagnostic in compiled.diagnostics}
    assert compiled.trace.inspection_shortlist_cap == 2


def test_bilibili_rcmd_is_rejected_as_exploration_supply() -> None:
    personalized = proposal(
        retrieval_plans=(RetrievalPlan(channel_refs=("bilibili:rcmd",), exploration=True),)
    )

    compiled = BriefCompiler((BILIBILI_MANIFEST,)).compile(personalized, now=NOW)

    assert not compiled.accepted
    assert "personalized-exploration" in {diagnostic.code for diagnostic in compiled.diagnostics}


def test_valid_brief_compiles_with_replayable_diagnostics() -> None:
    compiled = BriefCompiler((manifest(),), inspection_shortlist_cap=2).compile(proposal(), now=NOW)

    assert compiled.accepted
    assert compiled.diagnostics == ()
    assert compiled.trace.compiled_at == NOW
    assert {(item.channel_ref, item.bias_class) for item in compiled.trace.channels} == {
        ("demo:popular", BiasClass.PLATFORM_POPULARITY),
        ("demo:for-you", BiasClass.PLATFORM_PERSONALIZED),
    }


@pytest.mark.asyncio
async def test_shadow_service_journals_typed_proposal_and_compiler_trace(
    tmp_path: Path,
) -> None:
    path = tmp_path / "brief.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    try:
        journal = SqlitePolicyJournal(database)
        registry = HypothesisRegistry(journal, clock=lambda: NOW)
        active = await registry.register(
            arm="source-novel",
            statement="A new source may help",
            evidence_refs=("ev_1234567890abcdef1234567890abcdef",),
            falsification="no engagement",
            expires_at=NOW + timedelta(days=1),
        )
        await registry.record_outcome(active.hypothesis_id, "attempt", "one exposure")
        service = BriefService(
            runtime(TestModel(custom_output_args=proposal().model_dump(mode="json"))),
            registry,
            journal,
            BriefCompiler((manifest(),), inspection_shortlist_cap=2),
            clock=lambda: NOW,
        )

        compiled = await service.compile_shadow("replenishment:123")
        records = await journal.list_briefs(limit=10)

        assert compiled is not None and compiled.accepted
        assert records[0].status == "shadow"
        assert records[0].episode_id == "replenishment:123"
        assert records[0].payload["kind"] == "recommendation-brief"
        assert records[0].payload["proposal"] == proposal().model_dump(mode="json")
        assert records[0].payload["diagnostics"] == []
        compiler_trace = records[0].payload["compiler_trace"]
        assert isinstance(compiler_trace, dict)
        assert compiler_trace["inspection_shortlist_cap"] == 2
        assert compiler_trace["privacy_contract"] == "opaque-evidence-refs-only"
        agent_trace = records[0].payload["agent_trace"]
        assert isinstance(agent_trace, dict)
        assert agent_trace["agent_id"] == "recommendation.brief"
        assert len(cast("str", agent_trace["context_sha256"])) == 64

        rejected_proposal = proposal(
            retrieval_plans=(RetrievalPlan(channel_refs=("missing:unknown",), exploration=True),)
        )
        rejected = await BriefService(
            runtime(TestModel(custom_output_args=rejected_proposal.model_dump(mode="json"))),
            registry,
            journal,
            BriefCompiler((manifest(),), inspection_shortlist_cap=2),
            clock=lambda: NOW,
        ).compile_shadow("replenishment:rejected")
        rejected_record = (await journal.list_briefs(limit=10))[0]
        rejected_diagnostics = rejected_record.payload["diagnostics"]
        assert rejected is not None and not rejected.accepted
        assert rejected_record.status == "shadow"
        assert isinstance(rejected_diagnostics, list)
        assert isinstance(rejected_diagnostics[0], dict)
        assert rejected_diagnostics[0]["code"] == "unknown-channel"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_model_unavailable_is_a_silent_shadow_noop(tmp_path: Path) -> None:
    async def unavailable(_messages: object, _info: object) -> object:
        raise ConnectionError

    path = tmp_path / "unavailable.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    try:
        journal = SqlitePolicyJournal(database)
        service = BriefService(
            runtime(FunctionModel(cast("Any", unavailable))),
            HypothesisRegistry(journal, clock=lambda: NOW),
            journal,
            BriefCompiler((manifest(),)),
            clock=lambda: NOW,
        )

        assert await service.compile_shadow("replenishment:unavailable") is None
        assert await journal.list_briefs(limit=10) == ()
    finally:
        await database.close()
