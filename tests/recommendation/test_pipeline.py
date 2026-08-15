from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest

from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)
from openbiliclaw.content.integration.projections import ContentPreview, ProjectionProvenance
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.recommendation.discovery.planner import DiscoveryPlanner
from openbiliclaw.recommendation.evaluation.agent import (
    CandidateScore,
    EvaluationBatch,
    validate_complete,
)
from openbiliclaw.recommendation.evaluation.prefilter import normalize_and_prefilter
from openbiliclaw.recommendation.evaluation.service import EvaluationService
from openbiliclaw.recommendation.expression.agent import ExpressionBatch
from openbiliclaw.recommendation.expression.service import ExpressionService
from openbiliclaw.recommendation.jobs import recommendation_jobs
from openbiliclaw.recommendation.models import (
    AdmissionRecord,
    Candidate,
    CandidateState,
    DiscoveryProvenance,
    EvaluationRecord,
    FeedbackKind,
    FeedbackRecord,
    RejectionReason,
    SelectionRecord,
    ShownRecord,
    candidate_identity,
    record_identity,
)
from openbiliclaw.recommendation.repositories import SqliteRecommendationRepository
from openbiliclaw.recommendation.selection.service import SelectionService
from openbiliclaw.recommendation.service import RecommendationService
from openbiliclaw.understanding.profile import (
    CanonicalProfile,
    PreferenceClaim,
    PreferenceDimension,
    claim_id,
)
from openbiliclaw.understanding.projections import (
    DiscoveryProfile,
    discovery_projection,
    recommendation_projection,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def candidate(
    i: int,
    *,
    provider: str = "bilibili",
    title: str = "science",
    creator: str | None = None,
    topics: tuple[str, ...] = ("science",),
    state: CandidateState = CandidateState.DISCOVERED,
) -> Candidate:
    ref = ContentRef(
        provider_id=ProviderId(value=provider),
        content_kind=ContentKind(value="video"),
        provider_content_id=str(i),
        canonical_url=f"https://{provider}.example/{i}",
    )
    preview = ContentPreview(
        ref=ref,
        title=title,
        summary="summary",
        creator_label=creator,
        source_timestamp=NOW,
        provenance=ProjectionProvenance(ref=ref, native_schema_version=1, projected_at=NOW),
    )
    provenance = DiscoveryProvenance(
        strategy_id="search",
        query_key="science",
        provider=provider,
        channel=None,
        discovered_at=NOW,
    )
    return Candidate(
        candidate_id=candidate_identity(ref, "search", "science"),
        preview=preview,
        provenance=provenance,
        state=state,
        topics=topics,
        expires_at=NOW + timedelta(days=2),
    )


def evaluation(item: Candidate, score: float = 0.8) -> EvaluationRecord:
    return EvaluationRecord(
        evaluation_id=record_identity("eval", item.candidate_id),
        candidate_id=item.candidate_id,
        model_instance="test",
        rubric_version=1,
        context_version=1,
        score=score,
        rationale="good",
        uncertainty=0.1,
        input_tokens=1,
        output_tokens=1,
        evaluated_at=NOW,
    )


def test_state_machine_rejects_invalid_and_identity_is_stable() -> None:
    item = candidate(1)
    assert (
        item.transition(CandidateState.NORMALIZED).transition(CandidateState.PREFILTERED).state
        is CandidateState.PREFILTERED
    )
    with pytest.raises(ValueError, match="invalid candidate transition"):
        item.transition(CandidateState.SELECTED)
    assert candidate(1).candidate_id == item.candidate_id


def test_prefilter_hard_rules_precede_model() -> None:
    items = (candidate(1), candidate(1), candidate(2, title="blocked politics"), candidate(3))
    accepted, rejected = normalize_and_prefilter(
        items,
        seen_ids=frozenset({items[3].candidate_id}),
        blocked_urls=frozenset(),
        avoidances=("politics",),
        now=NOW,
    )
    assert len(accepted) == 1 and accepted[0].state is CandidateState.PREFILTERED
    assert {x[1] for x in rejected} == {
        RejectionReason.DUPLICATE,
        RejectionReason.AVOIDANCE,
        RejectionReason.SEEN,
    }


def test_evaluation_validation_and_failure_remains_retryable() -> None:
    output = EvaluationBatch(
        results=(CandidateScore(candidate_id="one", score=0.8, rationale="x", uncertainty=0.1),)
    )
    with pytest.raises(ValueError, match="exactly one"):
        validate_complete(output, ("one", "two"))


@pytest.mark.asyncio
async def test_evaluation_failure_leaves_prefiltered() -> None:
    async def broken(items: tuple[Candidate, ...]) -> tuple[EvaluationBatch, str, int, int]:
        raise RuntimeError("model down")

    pending = (candidate(1, state=CandidateState.PREFILTERED),)
    items, records = await EvaluationService(broken, lambda: NOW).evaluate(pending)
    assert items == pending and records == ()


@pytest.mark.asyncio
async def test_planner_defaults_dedupes_and_enforces_quota() -> None:
    manifest = ProviderManifest(
        provider_id=ProviderId(value="bilibili"),
        display_name="B",
        capabilities=frozenset({CapabilityKind.SEARCH}),
        native_schemas=(
            NativeSchemaDescriptor(content_kind=ContentKind(value="video"), schema_version=1),
        ),
        availability=ProviderAvailability.AVAILABLE,
    )
    profile = DiscoveryProfile(interests=(), avoidances=(), provider_preferences=())
    result = await DiscoveryPlanner().plan(
        profile, (manifest,), inventory_count=0, target_inventory=10, provider_quota=2
    )
    assert [x.text for x in result] == ["high quality recent content"]


def test_content_preferences_feed_discovery_and_recommendation_projections() -> None:
    preference = PreferenceClaim(
        claim_id=claim_id("preference", "content:Python 编程"),
        dimension=PreferenceDimension.CONTENT,
        value="Python 编程",
        confidence=0.9,
        fresh_at=NOW,
        evidence_ids=("ev_1234567890abcdef1234567890abcdef",),
    )
    profile = CanonicalProfile(
        profile_id="default", revision=1, updated_at=NOW, claims=(preference,)
    )

    assert discovery_projection(profile).interests == ("Python 编程",)
    assert recommendation_projection(profile).positive_topics == ("Python 编程",)


def test_selection_is_deterministic_and_cross_provider_fair() -> None:
    items = tuple(
        candidate(
            i,
            provider="bilibili" if i < 3 else "youtube",
            creator=f"c{i}",
            state=CandidateState.EVALUATED,
        )
        for i in range(4)
    )
    ev = tuple(evaluation(x, 0.9 - i * 0.01) for i, x in enumerate(items))
    service = SelectionService(provider_quota=1)
    first = service.select(items, ev, limit=4, seed=7, now=NOW)
    second = service.select(items, ev, limit=4, seed=7, now=NOW)
    assert first == second
    assert {x.preview.ref.provider_id.value for x in first[0]} == {"bilibili", "youtube"}
    assert all(r.contributions for r in first[2])


@pytest.mark.asyncio
async def test_expression_fallback_preserves_selection_identity() -> None:
    item = candidate(1, state=CandidateState.EVALUATED)
    selection = SelectionService().select((item,), (evaluation(item),), limit=1, seed=1, now=NOW)[2]

    async def broken(
        items: tuple[SelectionRecord, ...],
    ) -> tuple[ExpressionBatch, str]:
        raise RuntimeError

    result = await ExpressionService(broken, lambda: NOW).express(selection)
    assert tuple(x.recommendation_id for x in result) == tuple(
        x.recommendation_id for x in selection
    )
    assert result[0].model_instance is None


@pytest.mark.asyncio
async def test_repository_replay_feed_is_model_free(tmp_path: Path) -> None:
    path = tmp_path / "r.db"
    await SchemaMigrator(path).migrate()
    db = SqliteDatabase(path)
    await db.open()
    repo = SqliteRecommendationRepository(db)
    original = candidate(1, state=CandidateState.EVALUATED)
    assert await repo.add_candidate(original)
    assert not await repo.add_candidate(original)
    selected, admissions, selections = SelectionService().select(
        (original,), (evaluation(original),), limit=1, seed=1, now=NOW
    )
    persisted = await SelectionService().persist_selection(
        repo, (original,), admissions, selections
    )
    await repo.save_expression((await ExpressionService(None, lambda: NOW).express(selections))[0])
    assert persisted == selected and persisted[0].state is CandidateState.SELECTED
    shown = ShownRecord(
        shown_id=record_identity("shown", selections[0].recommendation_id),
        recommendation_id=selections[0].recommendation_id,
        candidate_id=original.candidate_id,
        shown_at=NOW,
    )
    await db.close()
    db2 = SqliteDatabase(path)
    await db2.open()
    feed = await RecommendationService(SqliteRecommendationRepository(db2)).deliver_feed(
        shown_at=NOW
    )
    assert tuple(item.selection for item in feed) == selections
    assert tuple(item.shown_id for item in feed) == (shown.shown_id,)
    assert tuple(item.reason for item in feed) == ("Recommended for relevance and freshness.",)
    assert tuple(item.selection.rank for item in feed) == (1,)
    restarted_repo = SqliteRecommendationRepository(db2)
    assert (await restarted_repo.load(original.candidate_id)).state is CandidateState.SHOWN
    feedback = FeedbackRecord(
        feedback_id=record_identity("feedback", shown.shown_id),
        shown_id=shown.shown_id,
        kind=FeedbackKind.LIKED,
        occurred_at=NOW,
    )
    assert await restarted_repo.save_feedback(feedback, original.preview.ref)
    assert not await restarted_repo.save_feedback(feedback, original.preview.ref)
    assert (await restarted_repo.load(original.candidate_id)).state is CandidateState.INTERACTED
    await db2.close()


@pytest.mark.asyncio
async def test_jobs_have_explicit_nonoverlap_and_budgets() -> None:
    async def run() -> None:
        return None

    jobs = recommendation_jobs(expiry=run, replenishment=run)
    assert {x.resource for x in jobs} == {"network", "database"}
    assert all(x.timeout_seconds > 0 and x.overlap_policy.value == "reject" for x in jobs)


@pytest.mark.asyncio
async def test_evaluation_success_and_expression_success() -> None:
    item = candidate(8, state=CandidateState.PREFILTERED)
    score = CandidateScore(
        candidate_id=item.candidate_id, score=0.82, rationale="fit", uncertainty=0.05
    )

    async def evaluator(items: tuple[Candidate, ...]) -> tuple[EvaluationBatch, str, int, int]:
        return EvaluationBatch(results=(score,)), "model-a", 10, 2

    evaluated, records = await EvaluationService(evaluator, lambda: NOW).evaluate((item,))
    assert evaluated[0].state is CandidateState.EVALUATED and records[0].model_instance == "model-a"
    selection = SelectionRecord(
        recommendation_id=record_identity("rec", item.candidate_id),
        candidate_id=item.candidate_id,
        rank=1,
        score=0.9,
        contributions=(),
        selected_at=NOW,
        seed=1,
    )
    from openbiliclaw.recommendation.expression.agent import ExpressedItem

    async def generate(items: tuple[SelectionRecord, ...]) -> tuple[ExpressionBatch, str]:
        return ExpressionBatch(
            items=(
                ExpressedItem(
                    recommendation_id=selection.recommendation_id,
                    reason="For your science interests",
                    tone="warm",
                ),
            )
        ), "model-b"

    expression = await ExpressionService(generate, lambda: NOW).express((selection,))
    assert expression[0].model_instance == "model-b"


@pytest.mark.asyncio
async def test_planner_generated_queries_dedupes_and_model_failure_falls_back() -> None:
    manifest = ProviderManifest(
        provider_id=ProviderId(value="bilibili"),
        display_name="B",
        capabilities=frozenset({CapabilityKind.SEARCH}),
        native_schemas=(),
        availability=ProviderAvailability.AVAILABLE,
    )
    from openbiliclaw.recommendation.discovery.query_agent import QueryBatch, QuerySuggestion

    async def generated() -> QueryBatch:
        return QueryBatch(
            suggestions=(
                QuerySuggestion(text="Science", topic="x"),
                QuerySuggestion(text="science", topic="x"),
            )
        )

    profile = DiscoveryProfile(interests=("default",), avoidances=(), provider_preferences=())
    plans = await DiscoveryPlanner().plan(
        profile,
        (manifest,),
        inventory_count=0,
        target_inventory=2,
        provider_quota=3,
        generate=generated,
    )
    assert len(plans) == 1 and plans[0].text == "Science"

    async def failed() -> QueryBatch:
        raise RuntimeError

    fallback = await DiscoveryPlanner().plan(
        profile,
        (manifest,),
        inventory_count=0,
        target_inventory=2,
        provider_quota=1,
        generate=failed,
    )
    assert fallback[0].text == "default"
    assert (
        await DiscoveryPlanner().plan(
            profile, (manifest,), inventory_count=2, target_inventory=2, provider_quota=1
        )
        == ()
    )


def test_prefilter_all_hard_rejection_reasons() -> None:
    malformed = candidate(9).model_copy(
        update={
            "preview": candidate(9).preview.model_copy(
                update={"source_timestamp": NOW + timedelta(days=1)}
            )
        }
    )
    stale = candidate(10).model_copy(update={"expires_at": NOW})
    inaccessible = candidate(11).model_copy(update={"accessible": False})
    unsupported = candidate(12).model_copy(update={"supported": False})
    blocked = candidate(13)
    accepted, rejected = normalize_and_prefilter(
        (malformed, stale, inaccessible, unsupported, blocked),
        seen_ids=frozenset(),
        blocked_urls=frozenset({blocked.preview.ref.canonical_url.casefold()}),
        avoidances=(),
        now=NOW,
    )
    assert not accepted
    assert {r for _, r in rejected} == {
        RejectionReason.MALFORMED,
        RejectionReason.STALE,
        RejectionReason.INACCESSIBLE,
        RejectionReason.UNSUPPORTED,
        RejectionReason.BLOCKED,
    }


@pytest.mark.asyncio
async def test_discovery_service_calls_search_capability_directly() -> None:
    from openbiliclaw.access.models import AccessHandle, AnonymousAccessHandle, Permission
    from openbiliclaw.content.integration.capabilities import ContentPage, SearchQuery
    from openbiliclaw.recommendation.discovery.planner import PlannedQuery
    from openbiliclaw.recommendation.discovery.service import DiscoveryService

    preview = candidate(9).preview

    class Search:
        async def search(
            self, query: SearchQuery, access: AccessHandle
        ) -> ContentPage[ContentPreview]:
            assert query.text == "science"
            return ContentPage(items=(preview,), next_cursor=None)

    access = AnonymousAccessHandle(
        provider_id="bilibili", account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )
    service = DiscoveryService(lambda provider_id: (Search(), access))
    result = await service.discover(
        (PlannedQuery(provider_id=ProviderId(value="bilibili"), text="science", topic="science"),)
    )
    assert tuple((item.preview, item.topic) for item in result) == ((preview, "science"),)
    with pytest.raises(ValueError):
        await service.discover((), limit=0)


@pytest.mark.asyncio
async def test_repository_all_aggregate_writes_and_edges(tmp_path: Path) -> None:
    path = tmp_path / "all.db"
    await SchemaMigrator(path).migrate()
    db = SqliteDatabase(path)
    await db.open()
    repo = SqliteRecommendationRepository(db)
    item = candidate(20)
    await repo.add_candidate(item)
    with pytest.raises(KeyError):
        await repo.load("missing")
    with pytest.raises(ValueError):
        await repo.transition(
            item.candidate_id, CandidateState.NORMALIZED, CandidateState.PREFILTERED
        )
    ev = evaluation(item)
    assert await repo.save_evaluation(ev)
    selection = SelectionRecord(
        recommendation_id=record_identity("rec", item.candidate_id),
        candidate_id=item.candidate_id,
        rank=1,
        score=0.9,
        contributions=(),
        selected_at=NOW,
        seed=1,
    )
    from openbiliclaw.recommendation.models import ExpressionRecord, FeedbackKind, FeedbackRecord

    expression = ExpressionRecord(
        recommendation_id=selection.recommendation_id, reason="x", tone="neutral", generated_at=NOW
    )
    await repo.save_expression(expression)
    feedback = FeedbackRecord(
        feedback_id=record_identity("feedback", item.candidate_id),
        shown_id="shown_" + "f" * 32,
        kind=FeedbackKind.LIKED,
        occurred_at=NOW,
    )
    with pytest.raises(KeyError):
        await repo.save_feedback(feedback, item.preview.ref)
    with pytest.raises(ValueError, match="unknown"):
        async with db.transaction() as session:
            await repo._insert_session(session, "unknown", "id", "x", "{}", NOW.isoformat())
    with pytest.raises(ValueError):
        await repo.deliver_feed(limit=0, shown_at=NOW)
    with pytest.raises(ValueError, match="evaluated"):
        await repo.admit_and_select(item, cast("Any", object()), selection)
    evaluated = item.model_copy(update={"state": CandidateState.EVALUATED})
    bad_admission = AdmissionRecord(
        admission_id=record_identity("admit", "other"),
        candidate_id="other",
        score=1,
        admitted_at=NOW,
    )
    with pytest.raises(ValueError, match="admission candidate"):
        await repo.admit_and_select(evaluated, bad_admission, selection)
    good_admission = bad_admission.model_copy(
        update={
            "admission_id": record_identity("admit", item.candidate_id),
            "candidate_id": item.candidate_id,
        }
    )
    bad_selection = selection.model_copy(update={"candidate_id": "other"})
    with pytest.raises(ValueError, match="selection candidate"):
        await repo.admit_and_select(evaluated, good_admission, bad_selection)
    expiring = candidate(21, state=CandidateState.SELECTED).model_copy(
        update={"expires_at": NOW + timedelta(seconds=1)}
    )
    future = candidate(22, state=CandidateState.ADMITTED).model_copy(
        update={"expires_at": NOW + timedelta(days=2)}
    )
    await repo.add_candidate(expiring)
    await repo.add_candidate(future)
    assert await repo.expire_due(now=(NOW + timedelta(seconds=2)).isoformat()) == 1
    assert (await repo.load(expiring.candidate_id)).state is CandidateState.EXPIRED
    await db.close()


def test_agent_validators_and_evaluation_batch_bound() -> None:
    from pydantic import ValidationError

    from openbiliclaw.recommendation.evaluation.agent import EvaluationInput
    from openbiliclaw.understanding.projections import RecommendationProfile

    duplicate = CandidateScore(candidate_id="same", score=0.5, rationale="x", uncertainty=0.1)
    with pytest.raises(ValidationError, match="duplicate"):
        EvaluationBatch(results=(duplicate, duplicate))
    with pytest.raises(ValidationError, match="align"):
        EvaluationInput(
            profile=RecommendationProfile(
                positive_topics=(),
                negative_topics=(),
                style_preferences=(),
                language_preferences=(),
            ),
            candidates=(candidate(1).preview,),
            candidate_ids=(),
        )

    async def unused(items: tuple[Candidate, ...]) -> tuple[EvaluationBatch, str, int, int]:
        raise AssertionError

    with pytest.raises(ValueError, match="20"):
        import asyncio

        asyncio.run(
            EvaluationService(unused, lambda: NOW).evaluate(tuple(candidate(i) for i in range(21)))
        )


def test_strategy_dispatch_covers_all_proven_kinds() -> None:
    from openbiliclaw.recommendation.discovery.strategies import StrategyConfig, StrategyKind

    provider = ProviderId(value="bilibili")
    seed = candidate(1).preview.ref
    configs = tuple(
        StrategyConfig(
            strategy_id=f"s{i}",
            kind=kind,
            provider_id=provider,
            quota=1,
            query="fixed" if kind is StrategyKind.DIRECT_PROVIDER else None,
            seed_ref=seed if kind is StrategyKind.RELATED_CHAIN else None,
        )
        for i, kind in enumerate(StrategyKind)
    )
    result = DiscoveryPlanner().dispatch(configs, "generated")
    assert [x.text for x in result] == [
        "generated",
        "trending",
        seed.provider_content_id,
        "generated",
        "fixed",
    ]
    with pytest.raises(ValueError, match="seed"):
        DiscoveryPlanner().dispatch(
            (
                StrategyConfig(
                    strategy_id="related",
                    kind=StrategyKind.RELATED_CHAIN,
                    provider_id=provider,
                    quota=1,
                ),
            ),
            "x",
        )
    with pytest.raises(ValueError, match="configured"):
        DiscoveryPlanner().dispatch(
            (
                StrategyConfig(
                    strategy_id="direct",
                    kind=StrategyKind.DIRECT_PROVIDER,
                    provider_id=provider,
                    quota=1,
                ),
            ),
            "x",
        )


@pytest.mark.asyncio
async def test_rejection_admission_constraints_and_transition_trigger(tmp_path: Path) -> None:
    import sqlite3

    from openbiliclaw.recommendation.evaluation.prefilter import persist_rejections

    path = tmp_path / "constraints.db"
    assert await SchemaMigrator(path).migrate() == 8
    db = SqliteDatabase(path)
    await db.open()
    repo = SqliteRecommendationRepository(db)
    item = candidate(30)
    await repo.add_candidate(item)
    rejected = item.transition(CandidateState.REJECTED)
    records = await persist_rejections(
        repo, repo, ((rejected, RejectionReason.MALFORMED),), now=NOW
    )
    assert records[0].reason is RejectionReason.MALFORMED
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO recommendation_admissions VALUES('bad','{}','x')")
        admission_id = "admit_" + "a" * 32
        connection.execute(
            "INSERT INTO recommendation_admissions VALUES(?,?,?)",
            (admission_id, "{}", "x"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO recommendation_admissions VALUES(?,?,?)",
                (admission_id, "{}", "x"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO recommendation_rejections VALUES('bad','{}','x')")
        with pytest.raises(sqlite3.IntegrityError, match="invalid recommendation"):
            connection.execute(
                "UPDATE recommendation_candidates SET state='selected' WHERE candidate_id=?",
                (item.candidate_id,),
            )
    await db.close()


def test_selection_applies_negative_preferences() -> None:
    item = candidate(40, title="blocked topic", state=CandidateState.EVALUATED)
    result = SelectionService().select(
        (item,), (evaluation(item),), limit=1, seed=1, now=NOW, negative_preferences=("blocked",)
    )
    assert result == ((), (), ())
