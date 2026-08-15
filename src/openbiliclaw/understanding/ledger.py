"""Auditable proposal decision ledger."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class LedgerStatus(StrEnum):
    ACCEPTED = "accepted"
    PENDING = "pending"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    RETIRED = "retired"
    OVERRIDE = "override"


class LedgerEntry(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ledger_id: str = Field(pattern=r"^ledger_[0-9a-f]{32}$")
    profile_id: str = Field(min_length=1, max_length=128)
    proposal_id: str | None = Field(default=None, pattern=r"^prop_[0-9a-f]{32}$")
    override_id: str | None = Field(default=None, pattern=r"^override_[0-9a-f]{32}$")
    claim_id: str = Field(pattern=r"^claim_[0-9a-f]{32}$")
    status: LedgerStatus
    reason: str = Field(min_length=1, max_length=100)
    decided_at: AwareDatetime
