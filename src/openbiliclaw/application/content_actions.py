"""Explicit pending-action proposal and confirmation workflows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.content.integration.actions import ActionResult  # noqa: TC001
from openbiliclaw.content.integration.identity import ContentRef  # noqa: TC001
from openbiliclaw.core._pydantic import StrictBaseModel

from .errors import ApplicationError, ApplicationErrorCode

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from openbiliclaw.access.models import AccessHandle


class PendingAction(StrictBaseModel):
    """Safe immutable confirmation record; never stores credentials or executable payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    pending_action_id: str = Field(pattern=r"^pending_[0-9a-f]{32}$")
    idempotency_key: str = Field(min_length=8, max_length=200)
    action_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    ref: ContentRef
    user_id: str = Field(min_length=1, max_length=128)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    safe_preview: str = Field(min_length=1, max_length=500)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    result: ActionResult | None = None


class PendingActionRepository(Protocol):
    async def put(self, action: PendingAction) -> PendingAction: ...
    async def get(self, pending_action_id: str) -> PendingAction | None: ...
    async def complete(self, action: PendingAction, result: ActionResult) -> PendingAction: ...


class AccessForAction(Protocol):
    def connected_handle(self, provider_id: str, account_id: str | None) -> AccessHandle | None: ...


class ContentStateVerifier(Protocol):
    async def available(self, ref: ContentRef, handle: AccessHandle) -> bool: ...


class ActionExecutor(Protocol):
    async def execute(self, pending: PendingAction, handle: AccessHandle) -> ActionResult: ...


class ProposeContentActionCommand(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: str = Field(min_length=8, max_length=200)
    action_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    ref: ContentRef
    user_id: str = Field(min_length=1, max_length=128)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    safe_preview: str = Field(min_length=1, max_length=500)
    expires_in_seconds: int = Field(default=300, ge=1, le=3600)


class ConfirmContentActionCommand(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pending_action_id: str = Field(pattern=r"^pending_[0-9a-f]{32}$")
    user_id: str = Field(min_length=1, max_length=128)


@dataclass(frozen=True, slots=True)
class ProposeContentAction:
    repository: PendingActionRepository
    clock: Callable[[], datetime]

    async def __call__(self, command: ProposeContentActionCommand) -> PendingAction:
        now = self.clock()
        identity = "pending_" + hashlib.sha256(command.idempotency_key.encode()).hexdigest()[:32]
        return await self.repository.put(
            PendingAction(
                pending_action_id=identity,
                idempotency_key=command.idempotency_key,
                action_id=command.action_id,
                ref=command.ref,
                user_id=command.user_id,
                account_id=command.account_id,
                safe_preview=command.safe_preview,
                created_at=now,
                expires_at=now.fromtimestamp(
                    now.timestamp() + command.expires_in_seconds, tz=now.tzinfo
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class ConfirmContentAction:
    repository: PendingActionRepository
    access: AccessForAction
    content: ContentStateVerifier
    executor: ActionExecutor
    clock: Callable[[], datetime]

    async def __call__(self, command: ConfirmContentActionCommand) -> ActionResult:
        pending = await self.repository.get(command.pending_action_id)
        if pending is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "pending action not found")
        if pending.user_id != command.user_id:
            raise ApplicationError(ApplicationErrorCode.FORBIDDEN, "pending action scope mismatch")
        if pending.result is not None:
            return pending.result
        if pending.expires_at <= self.clock():
            raise ApplicationError(ApplicationErrorCode.EXPIRED, "pending action expired")
        handle = self.access.connected_handle(pending.ref.provider_id.value, pending.account_id)
        if handle is None:
            raise ApplicationError(ApplicationErrorCode.UNAUTHORIZED, "access no longer available")
        if not await self.content.available(pending.ref, handle):
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "content is no longer actionable")
        # Executors must dedupe by pending.idempotency_key: a crash between
        # execute and complete re-executes on retry.
        result = await self.executor.execute(pending, handle)
        completed = await self.repository.complete(pending, result)
        if completed.result is None:
            raise RuntimeError("pending action completion failed")
        return completed.result


class InMemoryPendingActionRepository:
    """Small deterministic adapter used by composition until durable action wiring lands."""

    def __init__(self) -> None:
        self._actions: dict[str, PendingAction] = {}

    async def put(self, action: PendingAction) -> PendingAction:
        existing = self._actions.get(action.pending_action_id)
        if existing is not None and existing.idempotency_key != action.idempotency_key:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "pending identity conflict")
        self._actions.setdefault(action.pending_action_id, action)
        return self._actions[action.pending_action_id]

    async def get(self, pending_action_id: str) -> PendingAction | None:
        return self._actions.get(pending_action_id)

    async def complete(self, action: PendingAction, result: ActionResult) -> PendingAction:
        current = self._actions.get(action.pending_action_id)
        if current is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "pending action not found")
        if current.result is not None:
            return current
        completed = current.model_copy(update={"result": result})
        self._actions[action.pending_action_id] = completed
        return completed
