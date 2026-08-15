"""Typed recommendation pipeline state and audit records."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from openbiliclaw.content.integration.identity import (
    ContentRef,  # noqa: TC001  # Runtime type required by Pydantic model fields.
)
from openbiliclaw.content.integration.projections import (  # noqa: TC001  # Runtime type required by Pydantic model fields.
    CardData,
    ContentPreview,
)
from openbiliclaw.core._pydantic import StrictBaseModel


class CandidateState(StrEnum):
    DISCOVERED = "discovered"
    NORMALIZED = "normalized"
    PREFILTERED = "prefiltered"
    EVALUATED = "evaluated"
    ADMITTED = "admitted"
    REJECTED = "rejected"
    SELECTED = "selected"
    SHOWN = "shown"
    INTERACTED = "interacted"
    EXPIRED = "expired"


_ALLOWED: dict[CandidateState, frozenset[CandidateState]] = {
    CandidateState.DISCOVERED: frozenset({CandidateState.NORMALIZED, CandidateState.REJECTED}),
    CandidateState.NORMALIZED: frozenset({CandidateState.PREFILTERED, CandidateState.REJECTED}),
    CandidateState.PREFILTERED: frozenset({CandidateState.EVALUATED, CandidateState.REJECTED}),
    CandidateState.EVALUATED: frozenset({CandidateState.ADMITTED, CandidateState.REJECTED}),
    CandidateState.ADMITTED: frozenset({CandidateState.SELECTED, CandidateState.EXPIRED}),
    CandidateState.SELECTED: frozenset({CandidateState.SHOWN, CandidateState.EXPIRED}),
    CandidateState.SHOWN: frozenset({CandidateState.INTERACTED, CandidateState.EXPIRED}),
    CandidateState.REJECTED: frozenset(),
    CandidateState.INTERACTED: frozenset(),
    CandidateState.EXPIRED: frozenset(),
}


def candidate_identity(ref: ContentRef, strategy_id: str, query_key: str) -> str:
    value = f"{ref.provider_id.value}:{ref.provider_content_id}:{strategy_id}:{query_key}"
    return "cand_" + hashlib.sha256(value.encode()).hexdigest()[:32]


def record_identity(prefix: str, *values: str) -> str:
    return prefix + "_" + hashlib.sha256(":".join(values).encode()).hexdigest()[:32]


class ExplorationAttribution(StrictBaseModel):
    """The single exploration hypothesis/arm that supplied a candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str = Field(pattern=r"^hyp_[0-9a-f]{32}$")
    arm: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    channel: str | None = Field(default=None, min_length=1, max_length=512)


class DiscoveryProvenance(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    query_key: str = Field(min_length=1, max_length=500)
    provider: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    channel: str | None = Field(default=None, min_length=1, max_length=512)
    exploration: ExplorationAttribution | None = None
    discovered_at: AwareDatetime


class Candidate(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    preview: ContentPreview
    provenance: DiscoveryProvenance
    state: CandidateState = CandidateState.DISCOVERED
    topics: tuple[str, ...] = Field(default=(), max_length=20)
    accessible: bool = True
    supported: bool = True
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def identity_matches(self) -> Candidate:
        if self.candidate_id != candidate_identity(
            self.preview.ref, self.provenance.strategy_id, self.provenance.query_key
        ):
            raise ValueError("candidate identity must be deterministic")
        if self.expires_at <= self.provenance.discovered_at:
            raise ValueError("candidate expiry must follow discovery")
        return self

    def transition(self, target: CandidateState) -> Candidate:
        if target not in _ALLOWED[self.state]:
            raise ValueError(f"invalid candidate transition: {self.state}->{target}")
        return self.model_copy(update={"state": target})


class RejectionReason(StrEnum):
    MALFORMED = "malformed"
    BLOCKED = "blocked"
    SEEN = "seen"
    STALE = "stale"
    INACCESSIBLE = "inaccessible"
    UNSUPPORTED = "unsupported"
    DUPLICATE = "duplicate"
    AVOIDANCE = "avoidance"
    LOW_SCORE = "low_score"
    CAPACITY = "capacity"


class EvaluationRecord(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evaluation_id: str = Field(pattern=r"^eval_[0-9a-f]{32}$")
    candidate_id: str
    model_instance: str
    rubric_version: int = Field(ge=1)
    context_version: int = Field(ge=1)
    score: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=1000)
    uncertainty: float = Field(ge=0, le=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    evaluated_at: AwareDatetime


class RejectionRecord(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rejection_id: str = Field(pattern=r"^reject_[0-9a-f]{32}$")
    candidate_id: str
    reason: RejectionReason
    rejected_at: AwareDatetime


class AdmissionRecord(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    admission_id: str = Field(pattern=r"^admit_[0-9a-f]{32}$")
    candidate_id: str
    score: float = Field(ge=0, le=1)
    admitted_at: AwareDatetime


class ScoreContribution(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    component: str
    value: float


class SelectionRecord(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recommendation_id: str = Field(pattern=r"^rec_[0-9a-f]{32}$")
    candidate_id: str
    rank: int = Field(ge=1)
    score: float
    contributions: tuple[ScoreContribution, ...]
    selected_at: AwareDatetime
    seed: int


class RecommendationFeedItem(StrictBaseModel):
    """Delivered recommendation joined with its durable presentation projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    shown_id: str = Field(pattern=r"^shown_[0-9a-f]{32}$")
    selection: SelectionRecord
    ref: ContentRef
    card: CardData
    reason: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def matching_identity(self) -> RecommendationFeedItem:
        if self.ref != self.card.ref:
            raise ValueError("recommendation feed reference must match card")
        return self


class ShownRecord(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    shown_id: str = Field(pattern=r"^shown_[0-9a-f]{32}$")
    recommendation_id: str
    candidate_id: str
    shown_at: AwareDatetime


class FeedbackKind(StrEnum):
    OPENED = "opened"
    LIKED = "liked"
    DISLIKED = "disliked"
    SAVED = "saved"
    DISMISSED = "dismissed"


class FeedbackRecord(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    feedback_id: str = Field(pattern=r"^feedback_[0-9a-f]{32}$")
    shown_id: str
    kind: FeedbackKind
    occurred_at: AwareDatetime


class ExpressionRecord(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recommendation_id: str
    reason: str = Field(min_length=1, max_length=300)
    tone: str = Field(min_length=1, max_length=60)
    model_instance: str | None = None
    generated_at: AwareDatetime
