"""Decision provenance assembly and deterministic, model-free selection replay."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.recommendation.expression.service import ExpressionService
from openbiliclaw.recommendation.models import (
    Candidate,
    CandidateState,
    ExplorationAttribution,
    FeedbackKind,
    FeedbackRecord,
    SelectionRecord,
    record_identity,
)
from openbiliclaw.recommendation.repositories import SqliteRecommendationRepository
from openbiliclaw.recommendation.selection.service import SelectionService
from openbiliclaw.recommendation.service import RecommendationService
from openbiliclaw.recommendation.trace import assemble_trace, replay_selection
from tests.recommendation.test_pipeline import NOW, candidate, evaluation

if TYPE_CHECKING:
    from pathlib import Path


async def _persist_cohort(
    path: Path,
) -> tuple[
    SqliteDatabase,
    SqliteRecommendationRepository,
    tuple[Candidate, ...],
    tuple[SelectionRecord, ...],
]:
    await SchemaMigrator(path).migrate()
    db = SqliteDatabase(path)
    await db.open()
    repo = SqliteRecommendationRepository(db)
    candidates = tuple(
        candidate(
            index,
            provider=f"provider-{index}",
            topics=(f"topic-{index}",),
            state=CandidateState.EVALUATED,
        )
        for index in range(1, 4)
    )
    evaluations = tuple(
        evaluation(item, score=score)
        for item, score in zip(candidates, (0.91, 0.83, 0.74), strict=True)
    )
    for item, scored in zip(candidates, evaluations, strict=True):
        assert await repo.add_candidate(item)
        assert await repo.save_evaluation(scored)

    _, admissions, selections = SelectionService().select(
        candidates, evaluations, limit=3, seed=42, now=NOW
    )
    by_id = {item.candidate_id: item for item in candidates}
    selected_candidates = tuple(by_id[row.candidate_id] for row in selections)
    await SelectionService().persist_selection(repo, selected_candidates, admissions, selections)
    for expression in await ExpressionService(None, lambda: NOW).express(selections):
        await repo.save_expression(expression)
    await RecommendationService(repo).deliver_feed(limit=3, shown_at=NOW)

    first = selections[0]
    shown = await repo.load_shown(first.recommendation_id)
    feedback = FeedbackRecord(
        feedback_id=record_identity("feedback", shown.shown_id),
        shown_id=shown.shown_id,
        kind=FeedbackKind.LIKED,
        occurred_at=NOW,
    )
    assert await repo.save_feedback(feedback, by_id[first.candidate_id].preview.ref)
    return db, repo, candidates, selections


@pytest.mark.asyncio
async def test_assemble_trace_returns_complete_provenance_chain(tmp_path: Path) -> None:
    db, repo, _, selections = await _persist_cohort(tmp_path / "trace.db")
    try:
        selection = selections[0]
        trace = await assemble_trace(repo, selection.recommendation_id)

        assert trace.candidate.candidate_id == selection.candidate_id
        assert trace.evaluation.candidate_id == selection.candidate_id
        assert trace.admission.candidate_id == selection.candidate_id
        assert trace.selection == selection
        assert trace.expression is not None
        assert trace.expression.recommendation_id == selection.recommendation_id
        assert trace.shown is not None
        assert trace.shown.recommendation_id == selection.recommendation_id
        assert tuple(row.shown_id for row in trace.feedback) == (trace.shown.shown_id,)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_replay_selection_after_restart_matches_persisted_order(tmp_path: Path) -> None:
    path = tmp_path / "replay.db"
    db, _, _, selections = await _persist_cohort(path)
    await db.close()

    restarted = SqliteDatabase(path)
    await restarted.open()
    try:
        repo = SqliteRecommendationRepository(restarted)
        result = await replay_selection(repo, selections[0])
        expected = tuple(row.recommendation_id for row in selections)
        assert result.matched
        assert result.expected_ids == expected
        assert result.actual_ids == expected
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_replay_preserves_exploration_constraint(tmp_path: Path) -> None:
    path = tmp_path / "exploration-replay.db"
    await SchemaMigrator(path).migrate()
    db = SqliteDatabase(path)
    await db.open()
    try:
        repo = SqliteRecommendationRepository(db)
        attribution = ExplorationAttribution(
            hypothesis_id="hyp_" + "a" * 32,
            arm="source-novel",
        )
        exploit = candidate(20, state=CandidateState.EVALUATED)
        exploration = candidate(21, state=CandidateState.EVALUATED).model_copy(
            update={
                "provenance": candidate(21).provenance.model_copy(
                    update={"exploration": attribution.model_copy(update={"channel": "v2ex:hot"})}
                )
            }
        )
        other_channel = candidate(22, state=CandidateState.EVALUATED).model_copy(
            update={
                "provenance": candidate(22).provenance.model_copy(
                    update={
                        "exploration": attribution.model_copy(update={"channel": "bangumi:rank"})
                    }
                )
            }
        )
        candidates = (exploit, exploration, other_channel)
        evaluations = (
            evaluation(exploit, 0.99),
            evaluation(exploration, 0.61),
            evaluation(other_channel, 0.8),
        )
        for item, scored in zip(candidates, evaluations, strict=True):
            assert await repo.add_candidate(item)
            assert await repo.save_evaluation(scored)
        _, admissions, selections = SelectionService().select(
            candidates,
            evaluations,
            limit=3,
            seed=42,
            now=NOW,
            exploration=(attribution,),
        )
        by_id = {item.candidate_id: item for item in candidates}
        await SelectionService().persist_selection(
            repo,
            tuple(by_id[row.candidate_id] for row in selections),
            admissions,
            selections,
        )

        assert (await replay_selection(repo, selections[0])).matched
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_replay_detects_tampered_evaluation_score(tmp_path: Path) -> None:
    path = tmp_path / "tampered.db"
    db, _, _, selections = await _persist_cohort(path)
    tampered = selections[0]
    async with db.transaction() as session:
        row = await session.fetch_one(
            "SELECT record_json FROM recommendation_evaluations "
            "WHERE json_extract(record_json, '$.candidate_id') = ?",
            (tampered.candidate_id,),
        )
        assert row is not None
        payload = json.loads(str(row[0]))
        payload["score"] = 0.1
        await session.execute(
            "UPDATE recommendation_evaluations SET record_json = ? "
            "WHERE json_extract(record_json, '$.candidate_id') = ?",
            (json.dumps(payload), tampered.candidate_id),
        )
    try:
        result = await replay_selection(repo=SqliteRecommendationRepository(db), selection=tampered)
        assert not result.matched
        assert result.expected_ids != result.actual_ids
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_assemble_trace_missing_recommendation_raises_key_error(tmp_path: Path) -> None:
    path = tmp_path / "missing.db"
    await SchemaMigrator(path).migrate()
    db = SqliteDatabase(path)
    await db.open()
    try:
        repo = SqliteRecommendationRepository(db)
        with pytest.raises(KeyError, match="rec_missing"):
            await assemble_trace(repo, "rec_missing")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_replay_matches_when_original_cohort_had_unselected_members(
    tmp_path: Path,
) -> None:
    """Subset replay equivalence: 4 evaluated candidates, limit 3, distinct scores."""

    path = tmp_path / "subset.db"
    await SchemaMigrator(path).migrate()
    db = SqliteDatabase(path)
    await db.open()
    try:
        repo = SqliteRecommendationRepository(db)
        candidates = tuple(
            candidate(
                index,
                provider=f"provider-{index}",
                topics=(f"topic-{index}",),
                state=CandidateState.EVALUATED,
            )
            for index in range(1, 5)
        )
        evaluations = tuple(
            evaluation(item, score=score)
            for item, score in zip(candidates, (0.95, 0.85, 0.75, 0.65), strict=True)
        )
        for item, scored in zip(candidates, evaluations, strict=True):
            assert await repo.add_candidate(item)
            assert await repo.save_evaluation(scored)
        _, admissions, selections = SelectionService().select(
            candidates, evaluations, limit=3, seed=7, now=NOW
        )
        assert len(selections) == 3
        by_id = {item.candidate_id: item for item in candidates}
        await SelectionService().persist_selection(
            repo,
            tuple(by_id[row.candidate_id] for row in selections),
            admissions,
            selections,
        )

        result = await replay_selection(repo, selections[0])
        assert result.matched
        assert result.actual_ids == tuple(row.recommendation_id for row in selections)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_assemble_trace_before_delivery_has_no_expression_or_shown(
    tmp_path: Path,
) -> None:
    path = tmp_path / "early.db"
    await SchemaMigrator(path).migrate()
    db = SqliteDatabase(path)
    await db.open()
    try:
        repo = SqliteRecommendationRepository(db)
        item = candidate(1, state=CandidateState.EVALUATED)
        scored = evaluation(item, score=0.9)
        assert await repo.add_candidate(item)
        assert await repo.save_evaluation(scored)
        _, admissions, selections = SelectionService().select(
            (item,), (scored,), limit=1, seed=3, now=NOW
        )
        await SelectionService().persist_selection(repo, (item,), admissions, selections)

        trace = await assemble_trace(repo, selections[0].recommendation_id)
        assert trace.expression is None
        assert trace.shown is None
        assert trace.feedback == ()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_assemble_trace_missing_evaluation_or_admission_raises_key_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "partial.db"
    await SchemaMigrator(path).migrate()
    db = SqliteDatabase(path)
    await db.open()
    try:
        repo = SqliteRecommendationRepository(db)
        item = candidate(1, state=CandidateState.EVALUATED)
        scored = evaluation(item, score=0.9)
        assert await repo.add_candidate(item)

        with pytest.raises(KeyError):
            await repo.load_evaluation(item.candidate_id)
        with pytest.raises(KeyError):
            await repo.load_admission(item.candidate_id)
        with pytest.raises(KeyError):
            await repo.load_shown("rec_missing")
        with pytest.raises(KeyError):
            await repo.load_expression("rec_missing")
        assert await repo.save_evaluation(scored)
        with pytest.raises(KeyError):
            await repo.load_admission(item.candidate_id)
    finally:
        await db.close()
