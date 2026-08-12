"""Deterministic user edits which always outrank inference."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from openbiliclaw.core._pydantic import StrictBaseModel

if TYPE_CHECKING:
    from openbiliclaw.understanding.profile import CanonicalProfile


class OverrideOperation(StrEnum):
    SET = "set"
    REMOVE = "remove"


class UserOverride(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    override_id: str = Field(pattern=r"^override_[0-9a-f]{32}$")
    claim_id: str = Field(pattern=r"^claim_[0-9a-f]{32}$")
    operation: OverrideOperation
    value: str | None = Field(default=None, max_length=500)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def value_matches_operation(self) -> UserOverride:
        if self.operation is OverrideOperation.SET and not self.value:
            raise ValueError("set override requires a value")
        if self.operation is OverrideOperation.REMOVE and self.value is not None:
            raise ValueError("remove override cannot carry a value")
        return self

    @classmethod
    def create(
        cls,
        *,
        claim_id: str,
        operation: OverrideOperation,
        value: str | None,
        created_at: AwareDatetime,
    ) -> UserOverride:
        raw = f"{claim_id}:{operation.value}:{value or ''}:{created_at.isoformat()}"
        identity = "override_" + hashlib.sha256(raw.encode()).hexdigest()[:32]
        return cls(
            override_id=identity,
            claim_id=claim_id,
            operation=operation,
            value=value,
            created_at=created_at,
        )

    def apply(self, profile: CanonicalProfile) -> CanonicalProfile:
        claims = tuple(item for item in profile.claims if item.claim_id != self.claim_id)
        overrides = tuple(item for item in profile.overrides if item.claim_id != self.claim_id) + (
            self,
        )
        return profile.model_copy(
            update={
                "claims": claims,
                "overrides": overrides,
                "revision": profile.revision + 1,
                "updated_at": self.created_at,
            }
        )
