"""Persisted shown, feedback, and expiry state helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import Candidate, CandidateState, FeedbackKind, ShownRecord

if TYPE_CHECKING:
    from .repositories import InventoryRepository, ShownHistoryRepository


def apply_feedback(candidate: Candidate, kind: FeedbackKind) -> Candidate:
    if candidate.state is not CandidateState.SHOWN:
        raise ValueError("feedback requires shown candidate")
    return candidate.transition(CandidateState.INTERACTED)


async def mark_shown(repository: ShownHistoryRepository, record: ShownRecord) -> Candidate:
    return await repository.mark_shown(record)


async def expire_candidate(
    repository: InventoryRepository, candidate_id: str, expected: CandidateState
) -> Candidate:
    if expected not in {CandidateState.ADMITTED, CandidateState.SELECTED, CandidateState.SHOWN}:
        raise ValueError("only admitted, selected, or shown candidates expire")
    return await repository.expire(candidate_id, expected)
