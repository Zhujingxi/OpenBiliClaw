"""Model-friendly preference drafts and deterministic domain adaptation."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.understanding.profile import PreferenceClaim, PreferenceDimension, claim_id
from openbiliclaw.understanding.proposals import ClaimProposal, ProposalBatch, ProposalOwner

if TYPE_CHECKING:
    from datetime import datetime

    from openbiliclaw.understanding.evidence import EvidenceLink


class PreferenceDraft(StrictBaseModel):
    """Provider-generated data only; identities and timestamps are application-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: PreferenceDimension
    value: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=8)


class PreferenceDraftBatch(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposals: tuple[PreferenceDraft, ...] = Field(max_length=20)


def adapt_preference_drafts(
    batch: PreferenceDraftBatch,
    evidence: tuple[EvidenceLink, ...],
    agent_id: str,
    now: datetime,
) -> ProposalBatch:
    """Attach deterministic identities and discard hallucinated evidence references."""

    by_id = {item.evidence_id: item for item in evidence}
    proposals: list[ClaimProposal] = []
    for draft in batch.proposals:
        resolved = tuple(by_id[item] for item in draft.evidence_ids if item in by_id)
        if len(resolved) != len(draft.evidence_ids):
            continue
        identity = claim_id("preference", f"{draft.dimension.value}:{draft.value}")
        claim = PreferenceClaim(
            claim_id=identity,
            dimension=draft.dimension,
            value=draft.value,
            confidence=draft.confidence,
            fresh_at=now,
            evidence_ids=tuple(item.evidence_id for item in resolved),
        )
        proposal_digest = hashlib.sha256(f"{agent_id}:{identity}".encode()).hexdigest()[:32]
        proposals.append(
            ClaimProposal(
                proposal_id=f"prop_{proposal_digest}",
                analyzer_id=agent_id,
                owner=ProposalOwner.PREFERENCE,
                claim=claim,
                evidence=resolved,
                proposed_at=now,
            )
        )
    return ProposalBatch(proposals=tuple(proposals))
