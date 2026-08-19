"""Named deterministic ranking components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import Candidate, EvaluationRecord, ScoreContribution

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    candidate: Candidate
    evaluation: EvaluationRecord
    score: float
    contributions: tuple[ScoreContribution, ...]


def rank_candidates(
    candidates: tuple[Candidate, ...], evaluations: tuple[EvaluationRecord, ...], *, now: datetime
) -> tuple[RankedCandidate, ...]:
    by_id = {x.candidate_id: x for x in evaluations}
    result = []
    for item in candidates:
        evaluation = by_id.get(item.candidate_id)
        if evaluation is None:
            continue
        timestamp = item.preview.source_timestamp or item.provenance.discovered_at
        age = max(0.0, (now - timestamp).total_seconds() / 86400)
        freshness = max(0.0, 1 - age / 30)
        novelty = 0.05
        contrib = (
            ScoreContribution(component="model", value=evaluation.score),
            ScoreContribution(component="freshness", value=freshness * 0.15),
            ScoreContribution(component="novelty", value=novelty),
        )
        score = sum(x.value for x in contrib)
        result.append(RankedCandidate(item, evaluation, score, contrib))
    return tuple(sorted(result, key=lambda x: (-x.score, x.candidate.candidate_id)))
