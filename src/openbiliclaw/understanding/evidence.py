"""Bounded evidence provenance used by profile claims and analyzers."""

from __future__ import annotations

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class EvidenceLink(StrictBaseModel):
    """A compact, model-safe link to one immutable observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(pattern=r"^ev_[0-9a-f]{32}$")
    observation_id: str = Field(pattern=r"^obs_[0-9a-f]{32}$")
    summary: str = Field(min_length=1, max_length=500)
    occurred_at: AwareDatetime
    trust: float = Field(ge=0, le=1)
