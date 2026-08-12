"""Typed analyzer outputs; analyzers may propose but never persist profiles."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel

from .evidence import EvidenceLink  # noqa: TC001  # Runtime type required by Pydantic model fields.
from .profile import ProfileClaim  # noqa: TC001  # Runtime type required by Pydantic model fields.


class ProposalOwner(StrEnum):
    PREFERENCE = "preference"
    AVOIDANCE = "avoidance"
    TOPIC_LIFECYCLE = "topic_lifecycle"
    INSIGHT = "insight"


class ClaimProposal(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str = Field(pattern=r"^prop_[0-9a-f]{32}$")
    analyzer_id: str = Field(pattern=r"^understanding\.[a-z_]+\.v[1-9][0-9]*$")
    owner: ProposalOwner
    claim: ProfileClaim
    evidence: tuple[EvidenceLink, ...] = Field(max_length=64)
    proposed_at: AwareDatetime


class ProposalBatch(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposals: tuple[ClaimProposal, ...] = Field(max_length=50)
