"""Filtered dialogue evidence contract; normal chat is never learning evidence."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import TYPE_CHECKING

from openbiliclaw.observations.models import (
    AssistantFeedbackObservation,
    AssistantFeedbackPayload,
    PreferencePayload,
    PreferenceStatementObservation,
)
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)

if TYPE_CHECKING:
    from datetime import datetime


class DialogueObservationKind(StrEnum):
    NORMAL_MESSAGE = "normal_message"
    EXPLICIT_PREFERENCE = "explicit_preference"
    EXPLICIT_FEEDBACK = "explicit_feedback"
    CONFIRMED_EDIT = "confirmed_edit"
    DEFINED_OUTCOME = "defined_outcome"


def dialogue_observation(
    kind: DialogueObservationKind,
    text: str,
    conversation_id: str,
    occurred_at: datetime,
) -> PreferenceStatementObservation | AssistantFeedbackObservation | None:
    """Return evidence only for the four explicitly approved dialogue outcomes."""
    if kind is DialogueObservationKind.NORMAL_MESSAGE:
        return None
    identity = hashlib.sha256(f"{conversation_id}:{kind.value}:{text}".encode()).hexdigest()[:32]
    common = {
        "observation_id": f"obs_{identity}",
        "idempotency_key": f"assistant:{identity}",
        "occurred_at": occurred_at,
        "received_at": occurred_at,
        "account_id": None,
        "content_ref": None,
        "provenance": ObservationProvenance(
            producer_id="assistant.dialogue",
            source=ObservationSource.ASSISTANT,
            authenticated=False,
            trust_level=TrustLevel.LOW,
        ),
    }
    if kind in {
        DialogueObservationKind.EXPLICIT_PREFERENCE,
        DialogueObservationKind.CONFIRMED_EDIT,
    }:
        return PreferenceStatementObservation(**common, payload=PreferencePayload(statement=text))
    sentiment = "neutral" if kind is DialogueObservationKind.DEFINED_OUTCOME else "positive"
    return AssistantFeedbackObservation(
        **common,
        payload=AssistantFeedbackPayload(
            conversation_id=conversation_id,
            sentiment=sentiment,
            comment=text,
        ),
    )
