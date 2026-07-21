"""Revisioned evidence-profile contracts and deterministic merge rules."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

FacetName = Literal[
    "interests",
    "avoidances",
    "style_preferences",
    "values",
    "source_affinities",
]


class ProfileFacet(BaseModel):
    """One evidence-backed value in the user-controlled profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: FacetName
    value: str = Field(min_length=1, max_length=500)
    weight: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    overridden: bool = False

    @model_validator(mode="before")
    @classmethod
    def give_user_overrides_full_confidence(cls, data: object) -> object:
        """Make explicit user overrides authoritative regardless of input confidence."""

        if isinstance(data, Mapping) and data.get("overridden") is True:
            return {**dict(data), "confidence": 1.0}
        return data


class ProfileFacetEdit(BaseModel):
    """One explicit user-authored facet override without client-supplied evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: FacetName
    value: str = Field(min_length=1, max_length=500)
    weight: float

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("profile facet values cannot be empty")
        return normalized

    @field_validator("weight", mode="before")
    @classmethod
    def clamp_weight(cls, value: object) -> float:
        weight = float(value)  # type: ignore[arg-type]
        if not math.isfinite(weight):
            raise ValueError("profile facet weights must be finite")
        return max(-1.0, min(1.0, weight))


class ProfileFacetReference(BaseModel):
    """A typed case-insensitive facet identity to remove."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: FacetName
    value: str = Field(min_length=1, max_length=500)

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("profile facet values cannot be empty")
        return normalized


class ProfileEdit(BaseModel):
    """An explicit, optimistic edit that creates exactly one profile revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_revision: int | None = Field(ge=0)
    narrative: str | None = Field(default=None, max_length=10_000)
    upserts: tuple[ProfileFacetEdit, ...] = ()
    removals: tuple[ProfileFacetReference, ...] = ()

    @field_validator("narrative")
    @classmethod
    def normalize_narrative(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()

    @model_validator(mode="after")
    def normalize_actions(self) -> ProfileEdit:
        unique_upserts: dict[tuple[FacetName, str], ProfileFacetEdit] = {}
        for upsert in self.upserts:
            unique_upserts.setdefault((upsert.name, upsert.value.casefold()), upsert)
        unique_removals: dict[tuple[FacetName, str], ProfileFacetReference] = {}
        for removal in self.removals:
            unique_removals.setdefault((removal.name, removal.value.casefold()), removal)
        if unique_upserts.keys() & unique_removals.keys():
            raise ValueError("profile edit cannot remove and upsert the same facet")
        if self.narrative is None and not unique_upserts and not unique_removals:
            raise ValueError("profile edit must contain at least one change")
        object.__setattr__(self, "upserts", tuple(unique_upserts.values()))
        object.__setattr__(self, "removals", tuple(unique_removals.values()))
        return self


class ProfileSnapshot(BaseModel):
    """An immutable revision of the evidence profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    revision: int = Field(ge=0)
    narrative: str = ""
    facets: tuple[ProfileFacet, ...] = ()
    confidence: float = Field(default=0, ge=0, le=1)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class ProfileDelta(BaseModel):
    """An atomic, typed proposal for the next profile revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    narrative: str | None = None
    upserts: tuple[ProfileFacet, ...] = ()
    removals: tuple[tuple[FacetName, str], ...] = ()

    @model_validator(mode="after")
    def require_change(self) -> ProfileDelta:
        """Reject no-op proposals before they reach persistence."""

        if self.narrative is None and not self.upserts and not self.removals:
            raise ValueError("profile delta must contain at least one change")
        if any(not value.strip() for _, value in self.removals):
            raise ValueError("profile removal values cannot be empty")
        return self


def _facet_key(facet: ProfileFacet) -> tuple[FacetName, str]:
    return facet.name, facet.value.casefold()


def _merge_evidence(current: ProfileFacet, proposed: ProfileFacet) -> tuple[UUID, ...]:
    return tuple(dict.fromkeys((*current.evidence_ids, *proposed.evidence_ids)))


def _merge_facet(current: ProfileFacet, proposed: ProfileFacet) -> ProfileFacet:
    evidence_ids = _merge_evidence(current, proposed)
    if current.overridden and not proposed.overridden:
        return current.model_copy(update={"evidence_ids": evidence_ids})
    if proposed.overridden:
        return proposed.model_copy(update={"evidence_ids": evidence_ids})

    confidence_total = current.confidence + proposed.confidence
    weight = proposed.weight
    if confidence_total:
        weight = (
            current.weight * current.confidence + proposed.weight * proposed.confidence
        ) / confidence_total
    return proposed.model_copy(
        update={
            "weight": max(-1.0, min(1.0, weight)),
            "confidence": max(current.confidence, proposed.confidence),
            "evidence_ids": evidence_ids,
        }
    )


def apply_profile_delta(
    current: ProfileSnapshot,
    delta: ProfileDelta,
    *,
    created_at: datetime | None = None,
) -> ProfileSnapshot:
    """Apply a delta without deleting or weakening explicit user overrides."""

    revision_created_at = created_at or datetime.now(UTC)
    if revision_created_at.tzinfo is None or revision_created_at.utcoffset() is None:
        raise ValueError("profile revision timestamp must be timezone-aware")
    if revision_created_at <= current.created_at:
        raise ValueError("profile revision timestamp must be newer than its base revision")

    by_key: dict[tuple[FacetName, str], ProfileFacet] = {}
    for facet in current.facets:
        key = _facet_key(facet)
        existing = by_key.get(key)
        by_key[key] = facet if existing is None else _merge_facet(existing, facet)

    for name, value in delta.removals:
        key = (name, value.casefold())
        existing = by_key.get(key)
        if existing is not None and not existing.overridden:
            del by_key[key]

    for proposed in delta.upserts:
        key = _facet_key(proposed)
        existing = by_key.get(key)
        by_key[key] = proposed if existing is None else _merge_facet(existing, proposed)

    facets = tuple(
        sorted(
            by_key.values(),
            key=lambda facet: (facet.name, -facet.weight, facet.value.casefold(), facet.value),
        )
    )
    confidence = sum(facet.confidence for facet in facets) / len(facets) if facets else 0.0
    narrative = current.narrative if delta.narrative is None else delta.narrative.strip()
    return current.model_copy(
        update={
            "revision": current.revision + 1,
            "narrative": narrative,
            "facets": facets,
            "confidence": confidence,
            "created_at": revision_created_at,
        }
    )
