"""Shared observation provenance and trust metadata."""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class ObservationSource(StrEnum):
    RECOMMENDATION = "recommendation"
    HOST = "host"
    ASSISTANT = "assistant"
    PROFILE_EDITOR = "profile_editor"
    PROVIDER_IMPORT = "provider_import"


class TrustLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ObservationProvenance(StrictBaseModel):
    """Producer identity and authentication-derived trust."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    producer_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$", max_length=80)
    source: ObservationSource
    authenticated: bool
    trust_level: TrustLevel
    device_id: str | None = Field(default=None, min_length=1, max_length=128)
