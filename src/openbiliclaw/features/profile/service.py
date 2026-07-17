"""Evidence-profile projection and optimistic revision orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID, uuid4

from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind
from openbiliclaw.features.profile.domain import (
    FacetName,
    ProfileDelta,
    ProfileEdit,
    ProfileFacet,
    ProfileSnapshot,
    apply_profile_delta,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import TracebackType

    from openbiliclaw.features.activity.domain import ProfileSignal

_UNSET = object()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _next_revision_timestamp(
    observed_at: datetime,
    current: ProfileSnapshot | None,
) -> datetime:
    """Return an aware UTC timestamp newer than the current revision."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("profile revision clock must return an aware datetime")
    timestamp = observed_at.astimezone(UTC)
    if current is None:
        return timestamp
    current_timestamp = current.created_at.astimezone(UTC)
    return max(timestamp, current_timestamp + timedelta(microseconds=1))


class InvalidProfileDeltaError(ValueError):
    """Raised when a proposed delta violates deterministic evidence policy."""


class StaleProfileRevisionError(RuntimeError):
    """Raised when an AI proposal no longer targets the latest profile revision."""


class ProfileRepository(Protocol):
    def latest(self) -> ProfileSnapshot | None: ...

    def append(self, snapshot: ProfileSnapshot, expected_revision: int | None) -> None: ...

    def consumed_evidence_ids(self) -> frozenset[UUID]: ...

    def mark_evidence_consumed(
        self, evidence_ids: frozenset[UUID], *, profile_revision: int
    ) -> None: ...


class ActivityRepository(Protocol):
    def add(self, event: ActivityEvent) -> None: ...


class ProfileUnitOfWork(Protocol):
    profiles: ProfileRepository
    activities: ActivityRepository

    def __enter__(self) -> ProfileUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class ProfileDeltaAI(Protocol):
    """Typed AI port; infrastructure adapts the shared TaskRunner to it."""

    async def propose(
        self, profile: ProfileSnapshot, signals: tuple[ProfileSignal, ...]
    ) -> ProfileDelta: ...


def _signal_delta(signals: Sequence[ProfileSignal]) -> ProfileDelta:
    facets = tuple(
        ProfileFacet(
            name=cast("FacetName", signal.facet),
            value=signal.value,
            weight=signal.weight,
            confidence=signal.confidence,
            evidence_ids=signal.evidence_ids,
            overridden=signal.override,
        )
        for signal in signals
    )
    return ProfileDelta(upserts=facets)


def validate_profile_delta(delta: ProfileDelta, evidence_ids: frozenset[UUID]) -> None:
    """Reject hallucinated evidence, duplicate actions, and override fabrication."""

    upsert_keys = [(facet.name, facet.value.casefold()) for facet in delta.upserts]
    if len(set(upsert_keys)) != len(upsert_keys):
        raise InvalidProfileDeltaError("profile delta contains duplicate upserts")
    removal_keys = [(name, value.casefold()) for name, value in delta.removals]
    if len(set(removal_keys)) != len(removal_keys):
        raise InvalidProfileDeltaError("profile delta contains duplicate removals")
    if set(upsert_keys) & set(removal_keys):
        raise InvalidProfileDeltaError("profile delta cannot remove and upsert the same facet")
    for facet in delta.upserts:
        if len(set(facet.evidence_ids)) != len(facet.evidence_ids):
            raise InvalidProfileDeltaError("profile facet contains duplicate evidence IDs")
        if not set(facet.evidence_ids) <= evidence_ids:
            raise InvalidProfileDeltaError(
                "profile delta references evidence outside the projection"
            )


class ProfileService:
    """Apply one validated delta as one atomic optimistic profile revision."""

    def __init__(
        self,
        uow_factory: Callable[[], ProfileUnitOfWork],
        *,
        ai: ProfileDeltaAI | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._uow_factory = uow_factory
        self._ai = ai
        self._clock = clock

    def current(self) -> ProfileSnapshot | None:
        """Return the latest immutable evidence profile, if projected."""

        with self._uow_factory() as uow:
            return uow.profiles.latest()

    def edit(self, edit: ProfileEdit) -> ProfileSnapshot:
        """Apply one explicit user edit as authoritative evidence and one revision."""

        with self._uow_factory() as uow:
            current = uow.profiles.latest()
            actual_revision = None if current is None else current.revision
            if actual_revision != edit.expected_revision:
                raise StaleProfileRevisionError(
                    f"profile edit targeted revision {edit.expected_revision}, "
                    f"latest is {actual_revision}"
                )

            created_at = _next_revision_timestamp(self._clock(), current)
            evidence = ActivityEvent(
                source_id="local",
                kind=ActivityKind.PROFILE_OVERRIDE,
                occurred_at=created_at,
                title="Explicit profile edit",
                metadata={
                    "narrative_changed": edit.narrative is not None,
                    "upsert_count": len(edit.upserts),
                    "removal_count": len(edit.removals),
                },
            )
            uow.activities.add(evidence)

            facets = {
                (facet.name, facet.value.casefold()): facet
                for facet in (() if current is None else current.facets)
            }
            for removal in edit.removals:
                facets.pop((removal.name, removal.value.casefold()), None)
            for upsert in edit.upserts:
                facets[(upsert.name, upsert.value.casefold())] = ProfileFacet(
                    name=upsert.name,
                    value=upsert.value,
                    weight=upsert.weight,
                    confidence=1,
                    evidence_ids=(evidence.id,),
                    overridden=True,
                )
            ordered_facets = tuple(
                sorted(
                    facets.values(),
                    key=lambda facet: (
                        facet.name,
                        -facet.weight,
                        facet.value.casefold(),
                        facet.value,
                    ),
                )
            )
            confidence = (
                sum(facet.confidence for facet in ordered_facets) / len(ordered_facets)
                if ordered_facets
                else 0.0
            )
            if current is None:
                snapshot = ProfileSnapshot(
                    id=uuid4(),
                    revision=0,
                    narrative=edit.narrative or "",
                    facets=ordered_facets,
                    confidence=confidence,
                    created_at=created_at,
                )
            else:
                snapshot = current.model_copy(
                    update={
                        "revision": current.revision + 1,
                        "narrative": (
                            current.narrative if edit.narrative is None else edit.narrative
                        ),
                        "facets": ordered_facets,
                        "confidence": confidence,
                        "created_at": created_at,
                    }
                )
            uow.profiles.append(snapshot, expected_revision=actual_revision)
            uow.profiles.mark_evidence_consumed(
                frozenset({evidence.id}), profile_revision=snapshot.revision
            )
            uow.commit()
        return snapshot

    def apply_delta(
        self,
        delta: ProfileDelta,
        *,
        evidence_ids: frozenset[UUID],
        expected_base_revision: int | None | object = _UNSET,
        checkpoint: Callable[[], None] | None = None,
        transaction_guard: Callable[[object], None] | None = None,
    ) -> ProfileSnapshot:
        """Validate and append without splitting read/write across transactions."""

        validate_profile_delta(delta, evidence_ids)
        if checkpoint is not None:
            checkpoint()
        with self._uow_factory() as uow:
            if transaction_guard is not None:
                transaction_guard(uow)
            current = uow.profiles.latest()
            actual_revision = None if current is None else current.revision
            if expected_base_revision is not _UNSET and actual_revision != expected_base_revision:
                raise StaleProfileRevisionError(
                    f"profile proposal targeted revision {expected_base_revision}, "
                    f"latest is {actual_revision}"
                )
            expected_revision = actual_revision
            if current is None:
                snapshot = ProfileSnapshot(
                    id=uuid4(),
                    revision=0,
                    narrative=(delta.narrative or "").strip(),
                    facets=delta.upserts,
                    confidence=(
                        sum(facet.confidence for facet in delta.upserts) / len(delta.upserts)
                        if delta.upserts
                        else 0.0
                    ),
                )
            else:
                snapshot = apply_profile_delta(current, delta)
            uow.profiles.append(snapshot, expected_revision=expected_revision)
            uow.profiles.mark_evidence_consumed(evidence_ids, profile_revision=snapshot.revision)
            uow.commit()
        return snapshot

    async def project(
        self,
        signals: tuple[ProfileSignal, ...],
        *,
        checkpoint: Callable[[], None] | None = None,
        transaction_guard: Callable[[object], None] | None = None,
    ) -> ProfileSnapshot:
        """Optionally ask typed AI for a delta, then enforce application-owned policy."""

        if not signals:
            raise ValueError("profile projection requires evidence signals")
        with self._uow_factory() as uow:
            current = uow.profiles.latest()
        expected_base_revision = None if current is None else current.revision
        base = current or ProfileSnapshot(id=uuid4(), revision=0)
        delta = await self._ai.propose(base, signals) if self._ai else _signal_delta(signals)
        if self._ai and any(facet.overridden for facet in delta.upserts):
            raise InvalidProfileDeltaError("AI cannot create profile overrides")
        return self.apply_delta(
            delta,
            evidence_ids=frozenset(
                evidence_id for signal in signals for evidence_id in signal.evidence_ids
            ),
            expected_base_revision=expected_base_revision,
            checkpoint=checkpoint,
            transaction_guard=transaction_guard,
        )


__all__ = [
    "InvalidProfileDeltaError",
    "ProfileDeltaAI",
    "ProfileService",
    "StaleProfileRevisionError",
    "validate_profile_delta",
]
