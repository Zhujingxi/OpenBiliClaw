"""Immutable discriminated observation vocabulary."""

from __future__ import annotations

import re
from typing import Annotated, Literal, TypeAlias

from pydantic import AwareDatetime, ConfigDict, Field, TypeAdapter, field_validator

from openbiliclaw.content.integration.identity import (
    ContentRef,  # noqa: TC001  # Pydantic runtime field.
)
from openbiliclaw.core._pydantic import StrictBaseModel

from .provenance import ObservationProvenance  # noqa: TC001  # Pydantic runtime field.

_SAFE_TEXT = re.compile(
    r"(?i)(<[^>]+>|authorization\s*:|bearer\s+|ignore previous instructions|cookie\s*:)"
)


class EmptyPayload(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ShownPayload(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    batch_id: str = Field(min_length=1, max_length=128)
    position: int = Field(ge=0, le=10_000)


class OpenedPayload(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dwell_ms: int | None = Field(default=None, ge=0, le=86_400_000)


class ReasonPayload(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reason: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def safe_reason(cls, value: str | None) -> str | None:
        return _safe_text(value)


class HostOpenPayload(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    surface: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")


class AssistantFeedbackPayload(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    conversation_id: str = Field(min_length=1, max_length=128)
    sentiment: Literal["positive", "negative", "neutral"]
    comment: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("comment")
    @classmethod
    def safe_comment(cls, value: str | None) -> str | None:
        return _safe_text(value)


class PreferencePayload(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    statement: str = Field(min_length=1, max_length=1000)

    @field_validator("statement")
    @classmethod
    def safe_statement(cls, value: str) -> str:
        safe = _safe_text(value)
        if safe is None:
            raise ValueError("statement is required")
        return safe


class ProfileEditPayload(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    operation: Literal["set", "remove"]
    value: str | None = Field(default=None, max_length=1000)

    @field_validator("value")
    @classmethod
    def safe_value(cls, value: str | None) -> str | None:
        return _safe_text(value)


class HistoryImportPayload(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_event_id: str = Field(min_length=1, max_length=256)
    progress_seconds: int | None = Field(default=None, ge=0, le=31_536_000)


def _safe_text(value: str | None) -> str | None:
    if value is not None and _SAFE_TEXT.search(value):
        raise ValueError("text contains disallowed secret, markup, or instruction material")
    return value


class ObservationBase(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str = Field(pattern=r"^obs_[0-9a-f]{32}$")
    idempotency_key: str = Field(min_length=1, max_length=384)
    occurred_at: AwareDatetime
    received_at: AwareDatetime
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    content_ref: ContentRef | None = None
    provenance: ObservationProvenance
    schema_version: Literal[1] = 1


class RecommendationShownObservation(ObservationBase):
    event_type: Literal["recommendation_shown"] = "recommendation_shown"
    payload: ShownPayload


class RecommendationOpenedObservation(ObservationBase):
    event_type: Literal["recommendation_opened"] = "recommendation_opened"
    payload: OpenedPayload


class RecommendationLikedObservation(ObservationBase):
    event_type: Literal["recommendation_liked"] = "recommendation_liked"
    payload: EmptyPayload


class RecommendationDislikedObservation(ObservationBase):
    event_type: Literal["recommendation_disliked"] = "recommendation_disliked"
    payload: ReasonPayload


class RecommendationSavedObservation(ObservationBase):
    event_type: Literal["recommendation_saved"] = "recommendation_saved"
    payload: EmptyPayload


class RecommendationDismissedObservation(ObservationBase):
    event_type: Literal["recommendation_dismissed"] = "recommendation_dismissed"
    payload: ReasonPayload


class ContentOpenedObservation(ObservationBase):
    event_type: Literal["content_opened"] = "content_opened"
    payload: HostOpenPayload


class ContentSavedObservation(ObservationBase):
    event_type: Literal["content_saved"] = "content_saved"
    payload: EmptyPayload


class AssistantFeedbackObservation(ObservationBase):
    event_type: Literal["assistant_feedback"] = "assistant_feedback"
    payload: AssistantFeedbackPayload


class PreferenceStatementObservation(ObservationBase):
    event_type: Literal["preference_statement"] = "preference_statement"
    payload: PreferencePayload


class DeterministicProfileEditObservation(ObservationBase):
    event_type: Literal["deterministic_profile_edit"] = "deterministic_profile_edit"
    payload: ProfileEditPayload


class ProviderHistoryImportObservation(ObservationBase):
    event_type: Literal["provider_history_import"] = "provider_history_import"
    payload: HistoryImportPayload


Observation: TypeAlias = Annotated[
    RecommendationShownObservation
    | RecommendationOpenedObservation
    | RecommendationLikedObservation
    | RecommendationDislikedObservation
    | RecommendationSavedObservation
    | RecommendationDismissedObservation
    | ContentOpenedObservation
    | ContentSavedObservation
    | AssistantFeedbackObservation
    | PreferenceStatementObservation
    | DeterministicProfileEditObservation
    | ProviderHistoryImportObservation,
    Field(discriminator="event_type"),
]

observation_adapter: TypeAdapter[Observation] = TypeAdapter(Observation)
