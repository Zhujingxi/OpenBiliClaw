from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.models.test import TestModel
from tests.recommendation.test_prefilter_expression import candidate

from openbiliclaw.ai.runtime.capabilities import ModelCapabilities
from openbiliclaw.ai.runtime.execution import AIRuntime
from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable
from openbiliclaw.composition.jobs import (
    RecommendationPipeline,
    build_recommendation_jobs,
    build_understanding_job,
)
from openbiliclaw.content.integration.capabilities import ContentPage, FeedQuery
from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    BiasClass,
    CapabilityKind,
    ChannelDescriptor,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)
from openbiliclaw.core.resources import ResourceBudget
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.recommendation.allocation import AllocationDecision
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
from openbiliclaw.recommendation.discovery.service import DiscoveredPreview
from openbiliclaw.recommendation.evaluation.agent import EvaluationBatch
from openbiliclaw.recommendation.hypotheses import HypothesisRegistry
from openbiliclaw.recommendation.models import record_identity
from openbiliclaw.recommendation.policy_journal import SqlitePolicyJournal
from openbiliclaw.recommendation.repositories import SqliteRecommendationRepository
from openbiliclaw.recommendation.service import RecommendationService
from openbiliclaw.understanding.profile import AvoidanceClaim, CanonicalProfile, claim_id

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.content.integration.capabilities import SearchCapability

NOW = datetime(2030, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_model_free_evaluation_and_empty_pipeline_are_executable() -> None:
    batch, model, input_tokens, output_tokens = await RecommendationPipeline._evaluate_model_free(
        ()
    )
    assert batch == EvaluationBatch(results=())
    assert (model, input_tokens, output_tokens) == ("deterministic-baseline-v1", 0, 0)

    resolver = object.__new__(RecommendationPipeline)
    resolver_dynamic = cast("Any", resolver)
    resolver_dynamic._providers = SimpleNamespace(
        registry=SimpleNamespace(provider=lambda _provider: object())
    )
    resolver_dynamic._access = SimpleNamespace(connected_handle=lambda *_: None)
    with pytest.raises(RuntimeError, match="search"):
        resolver._resolve(ProviderId(value="demo"))
    provider = SimpleNamespace(search=AsyncMock())
    resolver_dynamic._providers.registry.provider = lambda _provider: provider
    resolver_dynamic._access.connected_handle = lambda *_: None
    with pytest.raises(RuntimeError, match="connected"):
        resolver._resolve(ProviderId(value="demo"))
    searchable = cast("SearchCapability", provider)
    handle = object()
    resolver_dynamic._providers.registry.provider = lambda _provider: searchable
    resolver_dynamic._access.connected_handle = lambda *_: handle
    assert resolver._resolve(ProviderId(value="demo")) == (searchable, handle)

    pipeline = object.__new__(RecommendationPipeline)
    dynamic = cast("Any", pipeline)
    avoidance = AvoidanceClaim(
        claim_id=claim_id("avoidance", "blocked"),
        value="blocked",
        confidence=0.9,
        fresh_at=NOW,
        evidence_ids=("ev_1234567890abcdef1234567890abcdef",),
    )
    dynamic._understanding = SimpleNamespace(
        profile=AsyncMock(
            return_value=CanonicalProfile(
                profile_id="default", revision=1, updated_at=NOW, claims=(avoidance,)
            )
        )
    )
    dynamic._providers = SimpleNamespace(registry=SimpleNamespace(manifests=lambda: ()))
    dynamic._access = SimpleNamespace(connected_handle=lambda *_: None)
    dynamic._target_count = 10
    dynamic._briefs = SimpleNamespace(
        compile_shadow=AsyncMock(side_effect=RuntimeError("shadow unavailable"))
    )
    dynamic._clock = lambda: NOW
    dynamic._allocate = AsyncMock(
        return_value=AllocationDecision(
            intent="uncertain",
            explore=False,
            arm=None,
            hypothesis_id=None,
            samples=(),
        )
    )
    dynamic._feed_supply = AsyncMock(return_value=())
    dynamic._planner = SimpleNamespace(plan=AsyncMock(return_value=()))
    dynamic._discovery = SimpleNamespace(discover=AsyncMock(return_value=()))
    dynamic._evaluation = SimpleNamespace(evaluate=AsyncMock(return_value=((), ())))
    dynamic._selection = SimpleNamespace(
        select=lambda *_args, **_kwargs: ((), (), ()), persist_selection=AsyncMock()
    )
    dynamic._expression = SimpleNamespace(express=AsyncMock(return_value=()))
    dynamic._repositories = SimpleNamespace(
        recommendations=SimpleNamespace(expire_due=AsyncMock(), add_candidate=AsyncMock())
    )

    await pipeline.replenish()
    dynamic._briefs.compile_shadow.assert_awaited_once()
    dynamic._allocate.assert_awaited_once()

    item = candidate("full")
    blocked = item.preview.model_copy(
        update={
            "title": "blocked but otherwise valid",
            "source_timestamp": datetime(2020, 1, 1, tzinfo=UTC),
        }
    )
    accepted_item = candidate("accepted")
    accepted_ref = accepted_item.preview.ref.model_copy(
        update={"provider_id": ProviderId(value="v2ex")}
    )
    accepted_preview = accepted_item.preview.model_copy(
        update={
            "ref": accepted_ref,
            "provenance": accepted_item.preview.provenance.model_copy(update={"ref": accepted_ref}),
            "source_timestamp": datetime(2020, 1, 1, tzinfo=UTC),
        }
    )
    dynamic._planner.plan.return_value = (
        SimpleNamespace(
            provider_id=blocked.ref.provider_id,
            text="science",
            topic="profile-topic",
        ),
    )
    dynamic._discovery.discover.return_value = (
        DiscoveredPreview(blocked, "profile-topic"),
        DiscoveredPreview(accepted_preview, "profile-topic"),
    )
    dynamic._evaluation.evaluate.return_value = ((), ())
    dynamic._repositories.recommendations.add_candidate = AsyncMock(return_value=True)
    dynamic._repositories.recommendations.transition = AsyncMock()
    dynamic._repositories.recommendations.save_evaluation = AsyncMock()
    dynamic._repositories.recommendations.save_expression = AsyncMock()
    selection_arguments: dict[str, object] = {}

    def select(*_args: object, **kwargs: object) -> tuple[tuple[()], tuple[()], tuple[()]]:
        selection_arguments.update(kwargs)
        return (), (), ()

    dynamic._selection.select = select
    dynamic._expression.express.return_value = (SimpleNamespace(),)
    result = await pipeline.replenish()
    inserted = tuple(
        call.args[0] for call in dynamic._repositories.recommendations.add_candidate.await_args_list
    )
    assert (result.discovered, result.added, result.selected) == (2, 2, 0)
    assert all(candidate.topics == ("profile-topic",) for candidate in inserted)
    assert all(
        candidate.provenance.provider == candidate.preview.ref.provider_id.value
        and candidate.provenance.channel is None
        for candidate in inserted
    )
    evaluated_candidates = dynamic._evaluation.evaluate.await_args.args[0]
    assert len(evaluated_candidates) == 1
    assert evaluated_candidates[0].preview.title == accepted_preview.title
    assert selection_arguments["negative_preferences"] == ("blocked",)
    await pipeline.expire()
    assert dynamic._selection.persist_selection.await_count == 2
    assert dynamic._repositories.recommendations.expire_due.await_count == 1

    dynamic._repositories.recommendations.add_candidate.return_value = False
    dynamic._evaluation.evaluate.reset_mock()
    duplicate_result = await pipeline.replenish()
    assert (duplicate_result.discovered, duplicate_result.added, duplicate_result.selected) == (
        2,
        0,
        0,
    )
    dynamic._evaluation.evaluate.assert_awaited_once_with(())


@pytest.mark.asyncio
async def test_replenish_journals_seeded_allocation_and_delivers_attributed_feed_supply(
    tmp_path: Path,
) -> None:
    clock = NOW + timedelta(microseconds=1)  # seed deterministically chooses source-novel
    path = tmp_path / "replenish.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    try:
        recommendations = SqliteRecommendationRepository(database)
        journal = SqlitePolicyJournal(database)
        hypotheses = HypothesisRegistry(journal, clock=lambda: clock)
        preview = candidate("provider-feed").preview
        manifest = ProviderManifest(
            provider_id=preview.ref.provider_id,
            display_name="Demo",
            capabilities=frozenset({CapabilityKind.FEED}),
            native_schemas=(
                NativeSchemaDescriptor(content_kind=ContentKind(value="video"), schema_version=1),
            ),
            channels=(
                ChannelDescriptor(
                    feed_id="hot",
                    bias_class=BiasClass.PLATFORM_POPULARITY,
                    auth_required=False,
                ),
            ),
            availability=ProviderAvailability.AVAILABLE,
        )

        class FakeFeedProvider:
            async def feed(self, query: FeedQuery, access: object) -> ContentPage[Any]:
                assert query.feed_id == "hot"
                assert access is not None
                return ContentPage(items=(preview,), next_cursor=None)

        provider = FakeFeedProvider()
        registry = SimpleNamespace(
            manifests=lambda: (manifest,),
            provider=lambda _provider_id: provider,
        )
        brief_proposal = RecommendationBrief(
            intent=BriefIntent.ENJOY,
            intent_expires_at=clock + timedelta(hours=1),
            hypotheses=(
                BriefHypothesis(
                    statement="The familiar baseline may satisfy this episode",
                    evidence_refs=("system:replenishment",),
                    falsification="explicit negative feedback",
                    expires_at=clock + timedelta(hours=2),
                ),
            ),
            retrieval_plans=(RetrievalPlan(channel_refs=("demo:hot",), exploration=False),),
            inspection_plan=InspectionPlan(
                shortlist_targets=(), quality_rubric="Prefer substantive content."
            ),
            slate_guidance=SlateGuidance(
                familiar_relationship="Stay near established interests.",
                novel_relationship="Allow only a close conceptual bridge.",
            ),
            action="recommend",
            stop_condition="Stop after this slate.",
            expires_at=clock + timedelta(hours=3),
        )
        model = ConfiguredModel(
            "brief-test",
            "test",
            TestModel(custom_output_args=brief_proposal.model_dump(mode="json")),
            ModelCapabilities(structured_output=True, context_tokens=8192),
        )
        runtime = AIRuntime(
            RouteTable((ModelRoute(BRIEF_AGENT.agent_id, BRIEF_AGENT.requirements, (model,)),)),
            ResourceBudget("model", 1),
        )
        briefs = BriefService(
            runtime,
            hypotheses,
            journal,
            BriefCompiler((manifest,)),
            clock=lambda: clock,
        )
        pipeline = RecommendationPipeline(
            cast("Any", SimpleNamespace(registry=registry)),
            cast("Any", SimpleNamespace(connected_handle=lambda *_args: object())),
            cast("Any", SimpleNamespace(recommendations=recommendations)),
            cast(
                "Any",
                SimpleNamespace(
                    profile=AsyncMock(
                        return_value=CanonicalProfile(
                            profile_id="default",
                            revision=1,
                            updated_at=clock,
                            claims=(),
                        )
                    )
                ),
            ),
            target_count=1,
            hypotheses=hypotheses,
            policy_journal=journal,
            briefs=briefs,
            clock=lambda: clock,
        )
        pipeline._planner = cast("Any", SimpleNamespace(plan=AsyncMock(return_value=())))
        pipeline._discovery = cast("Any", SimpleNamespace(discover=AsyncMock(return_value=())))

        result = await pipeline.replenish()
        delivered = await RecommendationService(recommendations).deliver_feed(
            limit=1, shown_at=clock
        )
        selected_candidate = await recommendations.load(delivered[0].selection.candidate_id)
        allocation = await journal.load_brief(
            record_identity("brief", f"allocation:{int(clock.timestamp() * 1_000_000)}")
        )
        shadow = next(
            record for record in await journal.list_briefs(limit=5) if record.status == "shadow"
        )

        assert result == type(result)(discovered=1, added=1, selected=1)
        assert shadow.payload["accepted"] is True
        shadow_proposal = shadow.payload["proposal"]
        assert isinstance(shadow_proposal, dict)
        assert shadow_proposal["intent"] == "enjoy"
        # Shadow evidence cannot switch the live B4 allocator from its pinned intent.
        assert allocation.payload["intent"] == "uncertain"
        assert allocation.payload["explore"] is True
        assert selected_candidate.provenance.channel == "demo:hot"
        assert selected_candidate.provenance.exploration is not None
        assert selected_candidate.provenance.exploration.arm == "source-novel"
        assert (
            selected_candidate.provenance.exploration.hypothesis_id
            == allocation.payload["hypothesis_id"]
        )
    finally:
        await database.close()


def test_recommendation_and_understanding_jobs_have_real_callbacks() -> None:
    pipeline = SimpleNamespace(replenish=AsyncMock(), expire=AsyncMock())
    jobs = build_recommendation_jobs(cast("Any", pipeline))
    assert tuple(job.job_id for job in jobs) == (
        "recommendation.replenishment",
        "recommendation.expiry",
    )
    understanding = SimpleNamespace(process=AsyncMock())
    job = build_understanding_job(cast("Any", understanding))
    assert job.job_id == "understanding.analysis"


@pytest.mark.asyncio
async def test_understanding_job_callback_processes_default_profile() -> None:
    understanding = SimpleNamespace(process=AsyncMock())
    job = build_understanding_job(cast("Any", understanding))
    await job.run()
    understanding.process.assert_awaited_once_with("default")
