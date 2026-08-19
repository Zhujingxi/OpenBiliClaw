"""Deterministic normalization and hard prefiltering before model calls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import (
    Candidate,
    CandidateState,
    RejectionReason,
    RejectionRecord,
    record_identity,
)

if TYPE_CHECKING:
    from datetime import datetime

    from ..repositories import EvaluationRepository, InventoryRepository


def normalize_and_prefilter(
    candidates: tuple[Candidate, ...],
    *,
    seen_ids: frozenset[str],
    blocked_urls: frozenset[str],
    avoidances: tuple[str, ...],
    now: datetime,
) -> tuple[tuple[Candidate, ...], tuple[tuple[Candidate, RejectionReason], ...]]:
    accepted: list[Candidate] = []
    rejected: list[tuple[Candidate, RejectionReason]] = []
    identities: set[tuple[str, str]] = set()
    urls: set[str] = set()
    blocked = {item.casefold() for item in avoidances}
    for item in candidates:
        ref = item.preview.ref
        identity = (ref.provider_id.value, ref.provider_content_id)
        url = ref.canonical_url.casefold()
        text = f"{item.preview.title} {item.preview.summary}".casefold()
        reason: RejectionReason | None = None
        if (
            not item.preview.title.strip()
            or not item.preview.ref.provider_content_id.strip()
            or (
                item.preview.source_timestamp is not None
                and item.preview.source_timestamp > item.provenance.discovered_at
            )
        ):
            reason = RejectionReason.MALFORMED
        elif identity in identities or url in urls:
            reason = RejectionReason.DUPLICATE
        elif item.candidate_id in seen_ids:
            reason = RejectionReason.SEEN
        elif url in blocked_urls:
            reason = RejectionReason.BLOCKED
        elif item.expires_at <= now:
            reason = RejectionReason.STALE
        elif not item.accessible:
            reason = RejectionReason.INACCESSIBLE
        elif not item.supported:
            reason = RejectionReason.UNSUPPORTED
        elif any(word in text for word in blocked):
            reason = RejectionReason.AVOIDANCE
        if reason is not None:
            rejected.append((item.transition(CandidateState.REJECTED), reason))
            continue
        identities.add(identity)
        urls.add(url)
        accepted.append(
            item.transition(CandidateState.NORMALIZED).transition(CandidateState.PREFILTERED)
        )
    return tuple(accepted), tuple(rejected)


async def persist_rejections(
    inventory: InventoryRepository,
    evaluations: EvaluationRepository,
    rejected: tuple[tuple[Candidate, RejectionReason], ...],
    *,
    now: datetime,
) -> tuple[RejectionRecord, ...]:
    records: list[RejectionRecord] = []
    for candidate, reason in rejected:
        current = await inventory.load(candidate.candidate_id)
        await inventory.transition(current.candidate_id, current.state, CandidateState.REJECTED)
        record = RejectionRecord(
            rejection_id=record_identity("reject", candidate.candidate_id, reason.value),
            candidate_id=candidate.candidate_id,
            reason=reason,
            rejected_at=now,
        )
        await evaluations.save_rejection(record)
        records.append(record)
    return tuple(records)
