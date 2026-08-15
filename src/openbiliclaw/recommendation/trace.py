"""Model-free recommendation provenance assembly and deterministic selection replay."""

from __future__ import annotations

from typing import Protocol

from pydantic import ConfigDict

from openbiliclaw.core._pydantic import StrictBaseModel

from .models import (
    AdmissionRecord,
    Candidate,
    CandidateState,
    EvaluationRecord,
    ExpressionRecord,
    FeedbackRecord,
    SelectionRecord,
    ShownRecord,
)
from .selection.service import SelectionService


class TraceRepository(Protocol):
    """Minimal read surface required to assemble and replay a recommendation decision."""

    async def load(self, candidate_id: str) -> Candidate: ...
    async def load_evaluation(self, candidate_id: str) -> EvaluationRecord: ...
    async def load_admission(self, candidate_id: str) -> AdmissionRecord: ...
    async def load_selection(self, recommendation_id: str) -> SelectionRecord: ...
    async def load_selections_for_seed(self, seed: int) -> tuple[SelectionRecord, ...]: ...
    async def load_expression(self, recommendation_id: str) -> ExpressionRecord: ...
    async def load_shown(self, recommendation_id: str) -> ShownRecord: ...
    async def load_feedback(self, recommendation_id: str) -> tuple[FeedbackRecord, ...]: ...


class DecisionTrace(StrictBaseModel):
    """Persisted provenance chain for one delivered recommendation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: Candidate
    evaluation: EvaluationRecord
    admission: AdmissionRecord
    selection: SelectionRecord
    expression: ExpressionRecord | None
    shown: ShownRecord | None
    feedback: tuple[FeedbackRecord, ...]


class ReplayResult(StrictBaseModel):
    """Comparison between persisted and deterministically replayed selection cohorts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    matched: bool
    expected_ids: tuple[str, ...]
    actual_ids: tuple[str, ...]


async def _optional_expression(
    repository: TraceRepository, recommendation_id: str
) -> ExpressionRecord | None:
    try:
        return await repository.load_expression(recommendation_id)
    except KeyError:
        return None


async def _optional_shown(
    repository: TraceRepository, recommendation_id: str
) -> ShownRecord | None:
    try:
        return await repository.load_shown(recommendation_id)
    except KeyError:
        return None


async def assemble_trace(repo: TraceRepository, recommendation_id: str) -> DecisionTrace:
    """Load the complete durable provenance chain for one recommendation."""

    selection = await repo.load_selection(recommendation_id)
    candidate_id = selection.candidate_id
    return DecisionTrace(
        candidate=await repo.load(candidate_id),
        evaluation=await repo.load_evaluation(candidate_id),
        admission=await repo.load_admission(candidate_id),
        selection=selection,
        expression=await _optional_expression(repo, recommendation_id),
        shown=await _optional_shown(repo, recommendation_id),
        feedback=await repo.load_feedback(recommendation_id),
    )


async def replay_selection(
    repo: TraceRepository, selection: SelectionRecord, *, threshold: float | None = None
) -> ReplayResult:
    """Rerun one persisted cohort with its original seed, limit, and clock.

    Caveats (persisted records do not carry every original input):
    - ``threshold`` must be the selection-time policy threshold (production wires 0.5
      in composition/jobs.py); omitted policy inputs such as ``negative_preferences``
      are not persisted and cannot be replayed yet (Phase B trace schema upgrade).
    - Only the *selected* subset is replayable; exact score ties among eligible-but-
      unselected candidates can legitimately reorder without tampering.
    """

    cohort = await repo.load_selections_for_seed(selection.seed)
    replay_candidates = [
        (await repo.load(row.candidate_id)).model_copy(update={"state": CandidateState.EVALUATED})
        for row in cohort
    ]
    evaluations = [await repo.load_evaluation(row.candidate_id) for row in cohort]
    service = SelectionService() if threshold is None else SelectionService(threshold=threshold)
    _, _, replayed = service.select(
        tuple(replay_candidates),
        tuple(evaluations),
        limit=len(cohort),
        seed=selection.seed,
        now=selection.selected_at,
    )
    expected_ids = tuple(row.recommendation_id for row in cohort)
    actual_ids = tuple(row.recommendation_id for row in replayed)
    return ReplayResult(
        matched=actual_ids == expected_ids,
        expected_ids=expected_ids,
        actual_ids=actual_ids,
    )
