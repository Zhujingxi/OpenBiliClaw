"""Lease-safe application service for generic browser source work."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

from pydantic import JsonValue, TypeAdapter

from openbiliclaw.features._metadata import freeze_metadata, serialize_metadata
from openbiliclaw.features.sources.domain import (
    BrowserOperationResult,
    BrowserOperationResultValue,
    ClaimedSourceTask,
    CredentialShapedPayloadError,
    SourceAccountDisconnectResult,
    SourceAccountStatus,
    SourceCredentialInput,
    SourceId,
    SourceManifest,
    SourceTaskCompletion,
    SourceTaskRequest,
    SourceTaskSnapshot,
    UnsupportedSourceOperationError,
    reject_credential_fields,
)
from openbiliclaw.features.system.domain import DEFAULT_DATABASE_BUSY_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import TracebackType

    from openbiliclaw.features.sources.registry import SourceRegistry

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class StaleSourceTaskLeaseError(RuntimeError):
    """Raised when a completion callback does not own the active task lease."""


class SourceTaskCompletionConflictError(RuntimeError):
    """Raised when a completed task receives a different duplicate result."""


class CancelledSourceTaskError(RuntimeError):
    """Raised when a cancelled durable task receives a completion callback."""


class AbandonedSourceTaskError(RuntimeError):
    """Raised when a request-deadline-expired task receives a completion callback."""


class SourceTaskRepository(Protocol):
    """Persistence operations required by the generic source-task service."""

    def add_pending(
        self,
        request: SourceTaskRequest,
        *,
        task_id: UUID,
        request_deadline_at: datetime,
        now: datetime,
    ) -> UUID: ...

    def claim(
        self,
        *,
        source_id: str,
        allowed_operations: frozenset[str],
        lease_token: str,
        lease_seconds: int,
    ) -> ClaimedSourceTask | None: ...

    def complete(
        self,
        *,
        task_id: UUID,
        lease_token: str,
        result: dict[str, JsonValue],
    ) -> SourceTaskCompletion: ...

    def get_snapshot(self, task_id: UUID) -> SourceTaskSnapshot: ...

    def cancel(self, task_id: UUID) -> SourceTaskSnapshot: ...


class SourceTaskUnitOfWork(Protocol):
    """Small transaction boundary needed by the source feature."""

    source_tasks: SourceTaskRepository

    def __enter__(self) -> SourceTaskUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class SourceAccountRepository(Protocol):
    def upsert_credentials(
        self, *, source_id: str, account_key: str, encrypted_credentials: object
    ) -> UUID: ...

    def list_statuses(self) -> tuple[SourceAccountStatus, ...]: ...

    def delete(self, *, source_id: str, account_key: str) -> bool: ...


class SourceAccountUnitOfWork(Protocol):
    source_accounts: SourceAccountRepository

    def __enter__(self) -> SourceAccountUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class CredentialCipherPort(Protocol):
    def encrypt(self, plaintext: str) -> object: ...


class SourceAccountService:
    """Configure source accounts while returning only secret-free state."""

    def __init__(
        self,
        uow_factory: Callable[[], SourceAccountUnitOfWork],
        *,
        cipher: CredentialCipherPort,
        registry: SourceRegistry | Callable[[], SourceRegistry],
    ) -> None:
        self._uow_factory = uow_factory
        self._cipher = cipher
        self._registry_provider = registry if callable(registry) else lambda: registry

    def manifests(self) -> tuple[SourceManifest, ...]:
        return tuple(self._registry_provider().manifests.values())

    def statuses(self) -> tuple[SourceAccountStatus, ...]:
        with self._uow_factory() as uow:
            return uow.source_accounts.list_statuses()

    def configure(
        self,
        source_id: SourceId,
        account_key: str,
        credentials: Mapping[str, object],
    ) -> SourceAccountStatus:
        self._registry_provider().get(source_id.value)
        key = account_key.strip()
        if not key:
            raise ValueError("source account key cannot be empty")
        validated = SourceCredentialInput.model_validate(dict(credentials), strict=True)
        plaintext = json.dumps(
            {"cookie": validated.cookie.get_secret_value()},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        ciphertext = self._cipher.encrypt(plaintext)
        with self._uow_factory() as uow:
            uow.source_accounts.upsert_credentials(
                source_id=source_id.value,
                account_key=key,
                encrypted_credentials=ciphertext,
            )
            uow.commit()
        return SourceAccountStatus(source_id=source_id, account_key=key, enabled=True)

    def disconnect(self, source_id: SourceId, account_key: str) -> SourceAccountDisconnectResult:
        """Delete encrypted account material; repeated calls remain successful and secret-free."""

        self._registry_provider().get(source_id.value)
        key = account_key.strip()
        if not key:
            raise ValueError("source account key cannot be empty")
        with self._uow_factory() as uow:
            deleted = uow.source_accounts.delete(source_id=source_id.value, account_key=key)
            uow.commit()
        return SourceAccountDisconnectResult(
            source_id=source_id,
            account_key=key,
            disconnected=True,
            idempotent=not deleted,
        )


class SourceTaskService:
    """Validate capabilities and own durable claim/complete lease semantics."""

    def __init__(
        self,
        uow_factory: Callable[[], SourceTaskUnitOfWork],
        registry: SourceRegistry | Callable[[], SourceRegistry],
        *,
        lease_seconds: int = 360,
        persistence_timeout_seconds: float = DEFAULT_DATABASE_BUSY_TIMEOUT_SECONDS,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("source task lease must be positive")
        if persistence_timeout_seconds <= 0:
            raise ValueError("source task persistence timeout must be positive")
        self._uow_factory = uow_factory
        self._registry_provider = registry if callable(registry) else lambda: registry
        self._lease_seconds = lease_seconds
        self._persistence_timeout_seconds = persistence_timeout_seconds

    @property
    def persistence_timeout_seconds(self) -> float:
        """Finite local persistence bound, configured to match SQLite busy timeout."""

        return self._persistence_timeout_seconds

    def enqueue(
        self,
        request: SourceTaskRequest,
        *,
        task_id: UUID | None = None,
        request_deadline_at: datetime | None = None,
    ) -> UUID:
        """Persist validated work only when the source advertises the operation."""

        connector = self._registry_provider().get(request.source_id)
        spec = connector.manifest.operation_spec(request.operation)
        if not spec.browser_assisted:
            raise UnsupportedSourceOperationError(
                f"{request.source_id.value} {request.operation.value} is not browser-assisted"
            )
        _safe_json_object(request.payload.model_dump(mode="json"))
        now = datetime.now(UTC)
        deadline = request_deadline_at or now + timedelta(seconds=self._lease_seconds)
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise ValueError("source task request deadline must be timezone-aware")
        resolved_task_id = task_id or uuid4()
        with self._uow_factory() as uow:
            persisted_id = uow.source_tasks.add_pending(
                request,
                task_id=resolved_task_id,
                request_deadline_at=deadline,
                now=now,
            )
            uow.commit()
        return persisted_id

    def claim(self, source_id: str) -> ClaimedSourceTask | None:
        """Lease the oldest pending or expired task for one canonical source."""

        connector = self._registry_provider().get(source_id)
        token = uuid4().hex
        with self._uow_factory() as uow:
            task = uow.source_tasks.claim(
                source_id=source_id,
                allowed_operations=frozenset(
                    spec.operation.value
                    for spec in connector.manifest.operations
                    if spec.browser_assisted
                ),
                lease_token=token,
                lease_seconds=self._lease_seconds,
            )
            uow.commit()
        return task

    def complete(
        self,
        task_id: UUID,
        lease_token: str,
        result: BrowserOperationResultValue,
    ) -> SourceTaskCompletion:
        """Complete once; identical retries succeed without rewriting the result."""

        with self._uow_factory() as uow:
            snapshot = uow.source_tasks.get_snapshot(task_id)
            raw_result = result.model_dump(mode="json")
            safe_result = _safe_json_object(raw_result)
            typed_result = BrowserOperationResult.validate_python(safe_result)
            serialized_result = typed_result.model_dump(mode="json")
            if snapshot.operation is not typed_result.operation:
                raise ValueError("source task completion operation does not match request")
            completion = uow.source_tasks.complete(
                task_id=task_id,
                lease_token=lease_token,
                result=serialized_result,
            )
            uow.commit()
        return completion

    def snapshot(self, task_id: UUID) -> SourceTaskSnapshot:
        """Read task state without exposing lease or persistence details."""

        with self._uow_factory() as uow:
            snapshot = uow.source_tasks.get_snapshot(task_id)
            uow.commit()
        return snapshot

    def cancel(self, task_id: UUID) -> SourceTaskSnapshot:
        """Make pending or leased browser work durably non-actionable."""

        with self._uow_factory() as uow:
            snapshot = uow.source_tasks.cancel(task_id)
            uow.commit()
        return snapshot


def _safe_json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    """Validate JSON recursively and reject credential-shaped keys without echoing values."""

    reject_credential_fields(value)
    frozen = freeze_metadata(value)
    result = _JSON_OBJECT.validate_python(serialize_metadata(frozen), strict=True)
    return result


def validate_source_task_payload(value: Mapping[str, object]) -> dict[str, JsonValue]:
    """Public transport-boundary validation for source-task request/result payloads."""

    return _safe_json_object(value)


# Re-export the request from the service module as the application-facing task API.
__all__ = [
    "AbandonedSourceTaskError",
    "CancelledSourceTaskError",
    "CredentialShapedPayloadError",
    "SourceTaskCompletionConflictError",
    "SourceTaskRequest",
    "SourceTaskService",
    "SourceAccountService",
    "StaleSourceTaskLeaseError",
    "validate_source_task_payload",
]
