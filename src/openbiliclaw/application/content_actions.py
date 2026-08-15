"""Explicit pending-action proposal and confirmation workflows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from openbiliclaw.content.integration.actions import (
    ActionResult,  # noqa: TC001  # Runtime type required by Pydantic model fields.
)
from openbiliclaw.content.integration.identity import (
    ContentRef,  # noqa: TC001  # Runtime type required by Pydantic model fields.
)
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.understanding.overrides import OverrideOperation
from openbiliclaw.understanding.profile import EXPLORATION_DISABLED_CLAIM_ID

from .edit_profile import EditProfileCommand, EditProfileResult
from .errors import ApplicationError, ApplicationErrorCode

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from openbiliclaw.access.models import AccessHandle
    from openbiliclaw.understanding.profile import CanonicalProfile


class ProfileRevision(StrictBaseModel):
    """Declarative profile correction captured for later human approval."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: str = Field(min_length=1, max_length=128)
    field: str = Field(pattern=r"^(claim_[0-9a-f]{32}|exploration\.disabled)$")
    operation: OverrideOperation
    value: str | None = Field(default=None, max_length=500)
    rationale: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def operation_has_valid_value(self) -> ProfileRevision:
        if self.operation is OverrideOperation.SET and not self.value:
            raise ValueError("set revision requires a value")
        if self.operation is OverrideOperation.REMOVE and self.value is not None:
            raise ValueError("remove revision cannot carry a value")
        if (
            self.field == "exploration.disabled"
            and self.operation is OverrideOperation.SET
            and self.value != "true"
        ):
            raise ValueError("exploration.disabled only accepts true")
        return self


class ProfileRevisionActionResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: Literal["profile_revision"] = "profile_revision"
    idempotency_key: str = Field(min_length=8, max_length=200)
    observation_id: str = Field(pattern=r"^obs_[0-9a-f]{32}$")
    completed_at: AwareDatetime


PendingActionResult = ActionResult | ProfileRevisionActionResult


class PendingAction(StrictBaseModel):
    """Safe immutable confirmation record; never stores credentials or executable payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    pending_action_id: str = Field(pattern=r"^pending_[0-9a-f]{32}$")
    idempotency_key: str = Field(min_length=8, max_length=200)
    kind: Literal["content", "profile_revision"] = "content"
    action_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    ref: ContentRef | None = None
    revision: ProfileRevision | None = None
    user_id: str = Field(min_length=1, max_length=128)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    safe_preview: str = Field(min_length=1, max_length=500)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    decision: Literal["pending", "approved", "rejected"] = "pending"
    result: PendingActionResult | None = None

    @model_validator(mode="before")
    @classmethod
    def restore_legacy_decision(cls, value: object) -> object:
        if isinstance(value, dict) and "decision" not in value and value.get("result") is not None:
            return {**value, "decision": "approved"}
        return value

    @model_validator(mode="after")
    def payload_matches_kind(self) -> PendingAction:
        if self.kind == "content" and (self.ref is None or self.revision is not None):
            raise ValueError("content action requires only a content ref")
        if self.kind == "profile_revision" and (self.ref is not None or self.revision is None):
            raise ValueError("profile revision requires only a revision payload")
        if self.decision == "approved" and self.result is None:
            raise ValueError("approved action requires a result")
        if self.decision != "approved" and self.result is not None:
            raise ValueError("only approved actions carry a result")
        return self


class PendingActionRepository(Protocol):
    async def put(self, action: PendingAction) -> PendingAction: ...
    async def get(self, pending_action_id: str) -> PendingAction | None: ...
    async def complete(
        self, action: PendingAction, result: PendingActionResult
    ) -> PendingAction: ...
    async def reject(self, action: PendingAction, *, decided_at: datetime) -> PendingAction: ...


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


class ProposeProfileRevisionCommand(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: str = Field(min_length=8, max_length=200)
    profile_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    field: str = Field(pattern=r"^(claim_[0-9a-f]{32}|exploration\.disabled)$")
    operation: OverrideOperation
    value: str | None = Field(default=None, max_length=500)
    rationale: str = Field(min_length=1, max_length=500)
    expires_in_seconds: int = Field(default=300, ge=1, le=3600)


class ConfirmProfileRevisionCommand(ConfirmContentActionCommand):
    pass


class RejectPendingActionCommand(ConfirmContentActionCommand):
    pass


class ProfileEditWorkflow(Protocol):
    async def __call__(self, command: EditProfileCommand) -> EditProfileResult: ...


class ProfileReader(Protocol):
    async def profile(self, profile_id: str) -> CanonicalProfile: ...


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
        if pending.kind != "content" or pending.ref is None:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "pending action kind mismatch")
        if pending.decision == "rejected":
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "pending action was rejected")
        if isinstance(pending.result, ActionResult):
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
        if not isinstance(completed.result, ActionResult):
            raise RuntimeError("pending action completion failed")
        return completed.result


@dataclass(frozen=True, slots=True)
class ProposeProfileRevision:
    repository: PendingActionRepository
    profiles: ProfileReader
    clock: Callable[[], datetime]

    async def __call__(self, command: ProposeProfileRevisionCommand) -> PendingAction:
        now = self.clock()
        if command.field != "exploration.disabled":
            profile = await self.profiles.profile(command.profile_id)
            if not any(item.claim_id == command.field for item in profile.claims):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "profile claim no longer exists"
                )
        revision = ProfileRevision(
            profile_id=command.profile_id,
            field=command.field,
            operation=command.operation,
            value=command.value,
            rationale=command.rationale,
        )
        identity = "pending_" + hashlib.sha256(command.idempotency_key.encode()).hexdigest()[:32]
        effect = f"Awaiting approval: {command.operation.value} {command.field}"
        if command.value is not None:
            effect += f" to {command.value}"
        return await self.repository.put(
            PendingAction(
                pending_action_id=identity,
                idempotency_key=command.idempotency_key,
                kind="profile_revision",
                action_id="profile_revision",
                revision=revision,
                user_id=command.user_id,
                account_id=command.account_id,
                safe_preview=effect,
                created_at=now,
                expires_at=now.fromtimestamp(
                    now.timestamp() + command.expires_in_seconds, tz=now.tzinfo
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class ConfirmProfileRevision:
    repository: PendingActionRepository
    edit_profile: ProfileEditWorkflow
    profiles: ProfileReader
    clock: Callable[[], datetime]

    async def __call__(self, command: ConfirmProfileRevisionCommand) -> ProfileRevisionActionResult:
        pending = await _pending_for_decision(self.repository, command)
        if pending.kind != "profile_revision" or pending.revision is None:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "pending action kind mismatch")
        if pending.decision == "rejected":
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "pending action was rejected")
        if isinstance(pending.result, ProfileRevisionActionResult):
            return pending.result
        if pending.expires_at <= self.clock():
            raise ApplicationError(ApplicationErrorCode.EXPIRED, "pending action expired")
        revision = pending.revision
        if revision.field != "exploration.disabled":
            profile = await self.profiles.profile(revision.profile_id)
            if not any(item.claim_id == revision.field for item in profile.claims):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "profile claim no longer exists"
                )
        claim_identity = (
            EXPLORATION_DISABLED_CLAIM_ID
            if revision.field == "exploration.disabled"
            else revision.field
        )
        account_id = pending.account_id
        if account_id is None:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "profile revision account missing"
            )
        edited = await self.edit_profile(
            EditProfileCommand(
                idempotency_key=_approval_idempotency_key(pending.idempotency_key),
                profile_id=revision.profile_id,
                account_id=account_id,
                claim_id=claim_identity,
                operation=revision.operation,
                value=revision.value,
            )
        )
        result = ProfileRevisionActionResult(
            idempotency_key=pending.idempotency_key,
            observation_id=edited.observation_id,
            completed_at=self.clock(),
        )
        completed = await self.repository.complete(pending, result)
        if not isinstance(completed.result, ProfileRevisionActionResult):
            raise RuntimeError("profile revision completion failed")
        return completed.result


@dataclass(frozen=True, slots=True)
class RejectPendingAction:
    repository: PendingActionRepository
    clock: Callable[[], datetime]

    async def __call__(self, command: RejectPendingActionCommand) -> PendingAction:
        pending = await _pending_for_decision(self.repository, command)
        if pending.decision == "approved":
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "pending action was approved")
        return await self.repository.reject(pending, decided_at=self.clock())


async def _pending_for_decision(
    repository: PendingActionRepository,
    command: ConfirmContentActionCommand,
) -> PendingAction:
    pending = await repository.get(command.pending_action_id)
    if pending is None:
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "pending action not found")
    if pending.user_id != command.user_id:
        raise ApplicationError(ApplicationErrorCode.FORBIDDEN, "pending action scope mismatch")
    return pending


def _approval_idempotency_key(idempotency_key: str) -> str:
    candidate = idempotency_key + ":approve"
    if len(candidate) <= 200:
        return candidate
    digest = hashlib.sha256(candidate.encode()).hexdigest()[:32]
    return f"{idempotency_key[:159]}:approve:{digest}"


class InMemoryPendingActionRepository:
    """Small deterministic pending-action adapter for unit tests."""

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

    async def complete(self, action: PendingAction, result: PendingActionResult) -> PendingAction:
        current = self._actions.get(action.pending_action_id)
        if current is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "pending action not found")
        if current.result is not None:
            return current
        completed = current.model_copy(update={"decision": "approved", "result": result})
        self._actions[action.pending_action_id] = completed
        return completed

    async def reject(self, action: PendingAction, *, decided_at: datetime) -> PendingAction:
        del decided_at
        current = self._actions.get(action.pending_action_id)
        if current is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "pending action not found")
        if current.decision != "pending":
            return current
        rejected = current.model_copy(update={"decision": "rejected"})
        self._actions[action.pending_action_id] = rejected
        return rejected
