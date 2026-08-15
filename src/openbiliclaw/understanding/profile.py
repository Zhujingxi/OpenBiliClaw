"""Frozen canonical user profile aggregate."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from openbiliclaw.core._pydantic import StrictBaseModel


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def claim_id(kind: str, value: str) -> str:
    """Return a stable claim identity independent of process/runtime."""

    digest = hashlib.sha256(f"{kind}:{_normalized(value)}".encode()).hexdigest()[:32]
    return f"claim_{digest}"


EXPLORATION_DISABLED_CLAIM_ID = claim_id("user_statement", "exploration.disabled")


class ClaimLifecycle(StrEnum):
    EMERGING = "emerging"
    ACTIVE = "active"
    RETIRED = "retired"


class PreferenceDimension(StrEnum):
    CONTENT = "content"
    STYLE = "style"
    CREATOR = "creator"
    LANGUAGE = "language"
    PROVIDER = "provider"


class ClaimBase(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(pattern=r"^claim_[0-9a-f]{32}$")
    confidence: float = Field(ge=0, le=1)
    fresh_at: AwareDatetime
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    lifecycle: ClaimLifecycle = ClaimLifecycle.ACTIVE


class StableInterestClaim(ClaimBase):
    kind: Literal["stable_interest"] = "stable_interest"
    value: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def deterministic_identity(self) -> StableInterestClaim:
        if self.claim_id != claim_id(self.kind, self.value):
            raise ValueError("claim identity must be deterministic")
        return self


class EmergingInterestClaim(ClaimBase):
    kind: Literal["emerging_interest"] = "emerging_interest"
    value: str = Field(min_length=1, max_length=200)
    lifecycle: ClaimLifecycle = ClaimLifecycle.EMERGING

    @model_validator(mode="after")
    def deterministic_identity(self) -> EmergingInterestClaim:
        if self.claim_id != claim_id(self.kind, self.value):
            raise ValueError("claim identity must be deterministic")
        return self


class AvoidanceClaim(ClaimBase):
    kind: Literal["avoidance"] = "avoidance"
    value: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def deterministic_identity(self) -> AvoidanceClaim:
        if self.claim_id != claim_id(self.kind, self.value):
            raise ValueError("claim identity must be deterministic")
        return self


class PreferenceClaim(ClaimBase):
    kind: Literal["preference"] = "preference"
    dimension: PreferenceDimension
    value: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def deterministic_identity(self) -> PreferenceClaim:
        if self.claim_id != claim_id(self.kind, f"{self.dimension.value}:{self.value}"):
            raise ValueError("claim identity must be deterministic")
        return self


class InsightClaim(ClaimBase):
    kind: Literal["insight"] = "insight"
    value: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def deterministic_identity(self) -> InsightClaim:
        if self.claim_id != claim_id(self.kind, self.value):
            raise ValueError("claim identity must be deterministic")
        return self


ProfileClaim: TypeAlias = Annotated[
    StableInterestClaim | EmergingInterestClaim | AvoidanceClaim | PreferenceClaim | InsightClaim,
    Field(discriminator="kind"),
]

# Imported late to avoid making overrides depend on profile construction internals.
from openbiliclaw.understanding.overrides import UserOverride  # noqa: E402, TC001


class CanonicalProfile(StrictBaseModel):
    """The sole durable representation of user understanding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    updated_at: AwareDatetime
    claims: tuple[ProfileClaim, ...] = Field(default=(), max_length=500)
    overrides: tuple[UserOverride, ...] = Field(default=(), max_length=500)

    @model_validator(mode="after")
    def unique_identities(self) -> CanonicalProfile:
        ids = [item.claim_id for item in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate claim identity")
        return self

    @classmethod
    def empty(cls, profile_id: str, now: AwareDatetime) -> CanonicalProfile:
        return cls(profile_id=profile_id, revision=0, updated_at=now)

    def exploration_disabled(self) -> bool:
        """Return whether the latest explicit statement disables exploration."""

        return any(
            item.claim_id == EXPLORATION_DISABLED_CLAIM_ID
            and item.operation.value == "set"
            and item.value == "true"
            for item in self.overrides
        )

    def lifecycle_for(self, identity: str) -> ClaimLifecycle:
        if any(item.claim_id == identity for item in self.claims):
            return next(item.lifecycle for item in self.claims if item.claim_id == identity)
        return ClaimLifecycle.RETIRED
