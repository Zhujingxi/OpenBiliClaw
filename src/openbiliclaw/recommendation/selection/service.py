"""Deterministic admission, exclusions, quotas, diversity, and selection."""

from __future__ import annotations

import random
from collections import Counter
from typing import TYPE_CHECKING

from ..models import (
    AdmissionRecord,
    Candidate,
    CandidateState,
    EvaluationRecord,
    ExplorationAttribution,
    SelectionRecord,
    record_identity,
)
from .ranking import RankedCandidate, rank_candidates

if TYPE_CHECKING:
    from datetime import datetime

    from ..repositories import RecommendationRepository


class SelectionService:
    def __init__(
        self,
        *,
        threshold: float = 0.6,
        provider_quota: int = 2,
        creator_quota: int = 1,
        topic_quota: int = 2,
    ) -> None:
        if not 0 <= threshold <= 1 or min(provider_quota, creator_quota, topic_quota) < 1:
            raise ValueError("invalid selection policy")
        self.threshold = threshold
        self.provider_quota = provider_quota
        self.creator_quota = creator_quota
        self.topic_quota = topic_quota

    def select(
        self,
        candidates: tuple[Candidate, ...],
        evaluations: tuple[EvaluationRecord, ...],
        *,
        limit: int,
        seed: int,
        now: datetime,
        seen_ids: frozenset[str] = frozenset(),
        negative_preferences: tuple[str, ...] = (),
        exploration: tuple[ExplorationAttribution, ...] = (),
    ) -> tuple[tuple[Candidate, ...], tuple[AdmissionRecord, ...], tuple[SelectionRecord, ...]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        by_id = {evaluation.candidate_id: evaluation for evaluation in evaluations}
        negatives = tuple(value.casefold() for value in negative_preferences)
        eligible = tuple(
            candidate
            for candidate in candidates
            if candidate.state is CandidateState.EVALUATED
            and candidate.candidate_id not in seen_ids
            and candidate.expires_at > now
            and candidate.candidate_id in by_id
            and by_id[candidate.candidate_id].score >= self.threshold
            and not any(
                value in f"{candidate.preview.title} {candidate.preview.summary}".casefold()
                for value in negatives
            )
        )
        ranked = list(rank_candidates(eligible, evaluations, now=now))
        random.Random(seed).shuffle(ranked)
        ranked.sort(key=lambda row: -row.score)
        providers: Counter[str] = Counter()
        creators: Counter[str] = Counter()
        topics: Counter[str] = Counter()
        selected = []
        selected_ids: set[str] = set()

        def add(row: RankedCandidate) -> bool:
            candidate = row.candidate
            if candidate.candidate_id in selected_ids:
                return False
            provider = candidate.preview.ref.provider_id.value
            creator = candidate.preview.creator_label or ""
            topic = candidate.topics[0] if candidate.topics else ""
            if (
                providers[provider] >= self.provider_quota
                or (creator and creators[creator] >= self.creator_quota)
                or (topic and topics[topic] >= self.topic_quota)
            ):
                return False
            selected.append(row)
            selected_ids.add(candidate.candidate_id)
            providers[provider] += 1
            creators[creator] += 1
            topics[topic] += 1
            return True

        for slot in exploration[:limit]:
            for row in ranked:
                attribution = row.candidate.provenance.exploration
                matches = (
                    attribution is not None
                    and attribution.hypothesis_id == slot.hypothesis_id
                    and attribution.arm == slot.arm
                    and (slot.channel is None or attribution.channel == slot.channel)
                )
                if matches and add(row):
                    break
        for row in ranked:
            if len(selected) >= limit:
                break
            add(row)
        admissions = tuple(
            AdmissionRecord(
                admission_id=record_identity("admit", row.candidate.candidate_id),
                candidate_id=row.candidate.candidate_id,
                score=row.evaluation.score,
                admitted_at=now,
            )
            for row in selected
        )
        records = tuple(
            SelectionRecord(
                recommendation_id=record_identity("rec", row.candidate.candidate_id, str(seed)),
                candidate_id=row.candidate.candidate_id,
                rank=index + 1,
                score=row.score,
                contributions=row.contributions,
                selected_at=now,
                seed=seed,
            )
            for index, row in enumerate(selected)
        )
        transitioned = tuple(
            row.candidate.transition(CandidateState.ADMITTED).transition(CandidateState.SELECTED)
            for row in selected
        )
        return transitioned, admissions, records

    async def persist_selection(
        self,
        repository: RecommendationRepository,
        evaluated: tuple[Candidate, ...],
        admissions: tuple[AdmissionRecord, ...],
        selections: tuple[SelectionRecord, ...],
    ) -> tuple[Candidate, ...]:
        if not (len(evaluated) == len(admissions) == len(selections)):
            raise ValueError("selection persistence records must align")
        return tuple(
            [
                await repository.admit_and_select(candidate, admission, selection)
                for candidate, admission, selection in zip(
                    evaluated, admissions, selections, strict=True
                )
            ]
        )
