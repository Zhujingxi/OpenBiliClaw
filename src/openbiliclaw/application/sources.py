"""Source connection workflows with explicit idempotency and recovery state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import ConfigDict, Field

from openbiliclaw.access.models import AccessHandle, AccessRequest, AccessStatus
from openbiliclaw.core._pydantic import StrictBaseModel


class AccessServicePort(Protocol):
    async def connect(
        self,
        request: AccessRequest,
        *,
        allowed_method_ids: frozenset[str],
        submission: dict[str, str] | None,
    ) -> AccessStatus: ...

    async def disconnect(self, provider_id: str, account_id: str | None) -> AccessStatus: ...

    def connected_handle(self, provider_id: str, account_id: str | None) -> AccessHandle | None: ...


class ProviderAvailabilityPort(Protocol):
    async def refresh(self, provider_id: str) -> None: ...


class IdempotencyJournal(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def put(self, key: str, value: str) -> None: ...


class ConnectSourceCommand(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: str = Field(min_length=8, max_length=200)
    request: AccessRequest
    allowed_method_ids: frozenset[str] = Field(min_length=1)
    submission: dict[str, str] | None = None


class ConnectSourceResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: AccessStatus
    availability_refreshed: bool
    recoverable: bool = False


class DisconnectSourceCommand(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: str = Field(min_length=8, max_length=200)
    provider_id: str = Field(min_length=1, max_length=128)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)


@dataclass(frozen=True, slots=True)
class ConnectSource:
    access: AccessServicePort
    availability: ProviderAvailabilityPort
    journal: IdempotencyJournal

    async def __call__(self, command: ConnectSourceCommand) -> ConnectSourceResult:
        cached = await self.journal.get(command.idempotency_key)
        if (
            cached is not None
            and self.access.connected_handle(
                command.request.provider_id, command.request.account_id
            )
            is not None
        ):
            return ConnectSourceResult.model_validate_json(cached)
        status = await self.access.connect(
            command.request,
            allowed_method_ids=command.allowed_method_ids,
            submission=command.submission,
        )
        refreshed = True
        try:
            await self.availability.refresh(command.request.provider_id)
        except Exception:
            # Credential state is already committed. Availability refresh is
            # retryable and is never rolled back by a transient provider error.
            refreshed = False
        result = ConnectSourceResult(
            status=status,
            availability_refreshed=refreshed,
            recoverable=not refreshed,
        )
        await self.journal.put(command.idempotency_key, result.model_dump_json())
        return result


@dataclass(frozen=True, slots=True)
class DisconnectSource:
    access: AccessServicePort
    journal: IdempotencyJournal

    async def __call__(self, command: DisconnectSourceCommand) -> AccessStatus:
        cached = await self.journal.get(command.idempotency_key)
        if cached is not None:
            return AccessStatus.model_validate_json(cached)
        status = await self.access.disconnect(command.provider_id, command.account_id)
        await self.journal.put(command.idempotency_key, status.model_dump_json())
        return status
