"""Application use cases for immutable activity evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind, ProfileSignal

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType
    from uuid import UUID


class ActivityRepository(Protocol):
    """Persistence port owned by the activity feature."""

    def add_if_absent(self, event: ActivityEvent) -> bool: ...

    def get_activity(self, event_id: UUID) -> ActivityEvent | None: ...


class ActivityUnitOfWork(Protocol):
    activities: ActivityRepository

    def __enter__(self) -> ActivityUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


# These evidence weights are product-policy seeds, not model calibration. They encode the
# ordering explicit override > explicit feedback/save > dwell/view. Recalibrate them against
# the offline feedback dataset whenever event semantics or the ranking model changes.
_EVIDENCE_WEIGHTS: dict[ActivityKind, tuple[float, float]] = {
    ActivityKind.IMPORT: (0.25, 0.4),
    ActivityKind.VIEW: (0.2, 0.35),
    ActivityKind.DWELL: (0.35, 0.5),
    ActivityKind.LIKE: (0.75, 0.85),
    ActivityKind.FAVORITE: (0.85, 0.9),
    ActivityKind.SEARCH: (0.45, 0.65),
    ActivityKind.FOLLOW: (0.7, 0.8),
    ActivityKind.FEEDBACK: (0.8, 0.9),
    ActivityKind.CHAT_LEARNING: (0.8, 0.9),
    ActivityKind.PROFILE_OVERRIDE: (1.0, 1.0),
}
_PROFILE_SIGNAL_VALUE_LIMIT = 500


def project_activity_event(event: ActivityEvent) -> tuple[ProfileSignal, ...]:
    """Project one normalized event to deterministic, evidence-linked signals."""

    metadata = event.model_dump(mode="json")["metadata"]
    value = str(metadata.get("value") or event.title or event.text or "").strip()
    if not value:
        return ()
    # Activity text is intentionally broader than one profile facet. Keep every
    # currently valid event ingestible and project a stable bounded prefix.
    value = value[:_PROFILE_SIGNAL_VALUE_LIMIT]
    override = event.kind is ActivityKind.PROFILE_OVERRIDE
    if override:
        facet = str(metadata.get("facet") or "interests")
    elif event.kind is ActivityKind.FOLLOW:
        facet = "source_affinities"
    elif event.kind is ActivityKind.FEEDBACK and str(metadata.get("sentiment")) == "negative":
        facet = "avoidances"
    else:
        facet = "interests"
    weight, confidence = _EVIDENCE_WEIGHTS[event.kind]
    if facet == "avoidances":
        weight = -abs(weight)
    metadata_weight = metadata.get("weight")
    if override and isinstance(metadata_weight, (int, float)):
        weight = max(-1.0, min(1.0, float(metadata_weight)))
    return (
        ProfileSignal(
            facet=facet,
            value=value,
            weight=weight,
            confidence=confidence,
            evidence_ids=(event.id,),
            override=override,
        ),
    )


class ActivityService:
    """Persist normalized events and return their deterministic profile evidence."""

    def __init__(self, uow_factory: Callable[[], ActivityUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def ingest(
        self,
        event: ActivityEvent,
        *,
        transaction_guard: Callable[[object], None] | None = None,
    ) -> tuple[ProfileSignal, ...]:
        """Store an immutable event before exposing its evidence projection."""

        with self._uow_factory() as uow:
            if transaction_guard is not None:
                transaction_guard(uow)
            inserted = uow.activities.add_if_absent(event)
            authoritative = event if inserted else uow.activities.get_activity(event.id)
            if authoritative is None:
                raise RuntimeError("activity conflict did not resolve to a persisted event")
            signals = project_activity_event(authoritative)
            uow.commit()
        return signals


__all__ = ["ActivityService", "project_activity_event"]
