"""Evaluation orchestration preserving pending work on model failure."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ..models import Candidate, CandidateState, EvaluationRecord, record_identity
from .agent import (
    EVALUATION_AGENT,
    MAX_EVALUATION_BATCH_SIZE,
    EvaluationBatch,
    validate_complete,
)

if TYPE_CHECKING:
    from datetime import datetime

Evaluator = Callable[[tuple[Candidate, ...]], Awaitable[tuple[EvaluationBatch, str, int, int]]]


class EvaluationService:
    def __init__(self, evaluate: Evaluator, clock: Callable[[], datetime]) -> None:
        self._evaluate = evaluate
        self._clock = clock

    async def evaluate(
        self, candidates: tuple[Candidate, ...]
    ) -> tuple[tuple[Candidate, ...], tuple[EvaluationRecord, ...]]:
        evaluated: list[Candidate] = []
        records: list[EvaluationRecord] = []
        for start in range(0, len(candidates), MAX_EVALUATION_BATCH_SIZE):
            batch_candidates, batch_records = await self._evaluate_batch(
                candidates[start : start + MAX_EVALUATION_BATCH_SIZE]
            )
            if not batch_records:
                return candidates, ()
            evaluated.extend(batch_candidates)
            records.extend(batch_records)
        return tuple(evaluated), tuple(records)

    async def _evaluate_batch(
        self, candidates: tuple[Candidate, ...]
    ) -> tuple[tuple[Candidate, ...], tuple[EvaluationRecord, ...]]:
        try:
            output, model, input_tokens, output_tokens = await self._evaluate(candidates)
            validate_complete(output, tuple(x.candidate_id for x in candidates))
        except Exception:
            return candidates, ()
        now = self._clock()
        by_id = {x.candidate_id: x for x in output.results}
        records = []
        for item in candidates:
            score = by_id[item.candidate_id]
            records.append(
                EvaluationRecord(
                    evaluation_id=record_identity(
                        "eval", item.candidate_id, str(EVALUATION_AGENT.rubric_version)
                    ),
                    candidate_id=item.candidate_id,
                    model_instance=model,
                    rubric_version=EVALUATION_AGENT.rubric_version,
                    context_version=EVALUATION_AGENT.context_version,
                    score=score.score,
                    rationale=score.rationale,
                    uncertainty=score.uncertainty,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    evaluated_at=now,
                )
            )
        return tuple(x.transition(CandidateState.EVALUATED) for x in candidates), tuple(records)
