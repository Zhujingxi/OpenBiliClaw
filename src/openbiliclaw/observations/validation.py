"""Shared observation metadata validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from .models import (
    ContentOpenedObservation,
    ContentSavedObservation,
    DeterministicProfileEditObservation,
    Observation,
    ProviderHistoryImportObservation,
)
from .provenance import ObservationSource, TrustLevel

if TYPE_CHECKING:
    from collections.abc import Callable


class ValidationCode(StrEnum):
    ACCEPTED = "accepted"
    EVENT_NOT_ALLOWED = "event_not_allowed"
    SOURCE_MISMATCH = "source_mismatch"
    MISSING_CONTENT = "missing_content"
    CLOCK_SKEW = "clock_skew"
    ACCOUNT_FORGERY = "account_forgery"
    INVALID_TRUST = "invalid_trust"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    code: ValidationCode
    message: str = ""

    @property
    def accepted(self) -> bool:
        return self.code is ValidationCode.ACCEPTED


_CONTENT_REQUIRED = frozenset(
    {
        "recommendation_shown",
        "recommendation_opened",
        "recommendation_liked",
        "recommendation_disliked",
        "recommendation_saved",
        "recommendation_dismissed",
        "content_opened",
        "content_saved",
        "provider_history_import",
    }
)


class ObservationValidator:
    """Validate shared trust, identity, time, and source invariants."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        maximum_future_skew: timedelta = timedelta(minutes=5),
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._maximum_future_skew = maximum_future_skew

    def validate(
        self, event: Observation, *, allowed_event_types: frozenset[str]
    ) -> ValidationResult:
        if event.event_type not in allowed_event_types:
            return ValidationResult(ValidationCode.EVENT_NOT_ALLOWED, "event not allowed")
        if event.occurred_at > self._now() + self._maximum_future_skew:
            return ValidationResult(ValidationCode.CLOCK_SKEW, "occurred_at exceeds clock skew")
        if event.received_at + self._maximum_future_skew < event.occurred_at:
            return ValidationResult(ValidationCode.CLOCK_SKEW, "received_at precedes occurred_at")
        if event.event_type in _CONTENT_REQUIRED and event.content_ref is None:
            return ValidationResult(ValidationCode.MISSING_CONTENT, "content reference required")
        provenance = event.provenance
        if not provenance.authenticated:
            if event.account_id is not None:
                return ValidationResult(
                    ValidationCode.ACCOUNT_FORGERY, "anonymous producer cannot set account"
                )
            if provenance.trust_level is not TrustLevel.LOW:
                return ValidationResult(
                    ValidationCode.INVALID_TRUST, "anonymous producer must be low trust"
                )
        expected_source = _expected_source(event)
        if expected_source is not None and provenance.source is not expected_source:
            return ValidationResult(
                ValidationCode.SOURCE_MISMATCH, "source does not match event type"
            )
        return ValidationResult(ValidationCode.ACCEPTED)


def _expected_source(event: Observation) -> ObservationSource | None:
    if isinstance(event, (ContentOpenedObservation, ContentSavedObservation)):
        return ObservationSource.HOST
    if isinstance(event, DeterministicProfileEditObservation):
        return ObservationSource.PROFILE_EDITOR
    if isinstance(event, ProviderHistoryImportObservation):
        return ObservationSource.PROVIDER_IMPORT
    if event.event_type in {"assistant_feedback", "preference_statement"}:
        return ObservationSource.ASSISTANT
    if event.event_type.startswith("recommendation_"):
        return ObservationSource.RECOMMENDATION
    return None
