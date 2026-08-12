"""Atomic deterministic profile override + audit observation workflow."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.observations.models import (
    DeterministicProfileEditObservation,
    ProfileEditPayload,
)
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.understanding.overrides import (
    OverrideOperation,  # noqa: TC001  # Runtime type required by Pydantic model fields.
)
from openbiliclaw.understanding.profile import (
    CanonicalProfile,  # noqa: TC001  # Runtime type required by Pydantic model fields.
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


class ProfileEditUnitOfWork(Protocol):
    async def edit_profile(
        self,
        profile_id: str,
        *,
        claim_id: str,
        operation: OverrideOperation,
        value: str | None,
        observation: DeterministicProfileEditObservation,
    ) -> CanonicalProfile: ...


class EditProfileCommand(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: str = Field(min_length=8, max_length=200)
    profile_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=128)
    claim_id: str = Field(pattern=r"^claim_[0-9a-f]{32}$")
    operation: OverrideOperation
    value: str | None = Field(default=None, max_length=500)


class EditProfileResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile: CanonicalProfile
    observation_id: str


@dataclass(frozen=True, slots=True)
class EditProfile:
    unit_of_work: ProfileEditUnitOfWork
    clock: Callable[[], datetime]

    async def __call__(self, command: EditProfileCommand) -> EditProfileResult:
        now = self.clock()
        observation_id = "obs_" + hashlib.sha256(command.idempotency_key.encode()).hexdigest()[:32]
        event = DeterministicProfileEditObservation(
            observation_id=observation_id,
            idempotency_key=command.idempotency_key,
            occurred_at=now,
            received_at=now,
            account_id=command.account_id,
            content_ref=None,
            provenance=ObservationProvenance(
                producer_id="application.profile_editor",
                source=ObservationSource.PROFILE_EDITOR,
                authenticated=True,
                trust_level=TrustLevel.HIGH,
            ),
            payload=ProfileEditPayload(
                field=command.claim_id,
                operation=command.operation.value,
                value=command.value,
            ),
        )
        profile = await self.unit_of_work.edit_profile(
            command.profile_id,
            claim_id=command.claim_id,
            operation=command.operation,
            value=command.value,
            observation=event,
        )
        return EditProfileResult(profile=profile, observation_id=observation_id)
