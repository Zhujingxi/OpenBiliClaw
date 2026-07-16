"""Lease-safe application service for generic browser source work."""

from __future__ import annotations

import re
from collections.abc import Mapping as MappingABC
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

from pydantic import JsonValue, TypeAdapter

from openbiliclaw.features.sources.domain import (
    ClaimedSourceTask,
    SourceTaskCompletion,
    SourceTaskRequest,
    SourceTaskSnapshot,
    SourceTransportKind,
    UnsupportedSourceOperationError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import TracebackType

    from openbiliclaw.features.sources.registry import SourceRegistry

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_CREDENTIAL_FIELD_SUFFIXES = (
    "apikey",
    "apikeys",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "passwords",
    "proxyauthorization",
    "secret",
    "secrets",
    "session",
    "sessions",
    "token",
    "tokens",
)


class CredentialShapedPayloadError(ValueError):
    """Raised before credential-like task data can reach persistence or logs."""


class StaleSourceTaskLeaseError(RuntimeError):
    """Raised when a completion callback does not own the active task lease."""


class SourceTaskCompletionConflictError(RuntimeError):
    """Raised when a completed task receives a different duplicate result."""


class SourceTaskRepository(Protocol):
    """Persistence operations required by the generic source-task service."""

    def add_pending(self, request: SourceTaskRequest, *, now: datetime) -> UUID: ...

    def claim(
        self,
        *,
        source_id: str,
        allowed_operations: frozenset[str],
        lease_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> ClaimedSourceTask | None: ...

    def complete(
        self,
        *,
        task_id: UUID,
        lease_token: str,
        result: dict[str, JsonValue],
        now: datetime,
    ) -> SourceTaskCompletion: ...

    def get_snapshot(self, task_id: UUID) -> SourceTaskSnapshot: ...


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


class SourceTaskService:
    """Validate capabilities and own durable claim/complete lease semantics."""

    def __init__(
        self,
        uow_factory: Callable[[], SourceTaskUnitOfWork],
        registry: SourceRegistry | Callable[[], SourceRegistry],
        *,
        lease_seconds: int = 360,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("source task lease must be positive")
        self._uow_factory = uow_factory
        self._registry_provider = registry if callable(registry) else lambda: registry
        self._lease_seconds = lease_seconds

    def enqueue(self, request: SourceTaskRequest) -> UUID:
        """Persist validated work only when the source advertises the operation."""

        connector = self._registry_provider().get(request.source_id)
        spec = connector.manifest.operation_spec(request.operation)
        if spec.transport_kind is not SourceTransportKind.BROWSER:
            raise UnsupportedSourceOperationError(
                f"{request.source_id.value} {request.operation.value} is not browser-assisted"
            )
        _safe_json_object(request.payload)
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            task_id = uow.source_tasks.add_pending(request, now=now)
            uow.commit()
        return task_id

    def claim(self, source_id: str) -> ClaimedSourceTask | None:
        """Lease the oldest pending or expired task for one canonical source."""

        connector = self._registry_provider().get(source_id)
        now = datetime.now(UTC)
        token = uuid4().hex
        with self._uow_factory() as uow:
            task = uow.source_tasks.claim(
                source_id=source_id,
                allowed_operations=frozenset(
                    spec.operation.value
                    for spec in connector.manifest.operations
                    if spec.transport_kind is SourceTransportKind.BROWSER
                ),
                lease_token=token,
                now=now,
                lease_expires_at=now + timedelta(seconds=self._lease_seconds),
            )
            uow.commit()
        return task

    def complete(
        self,
        task_id: UUID,
        lease_token: str,
        result: Mapping[str, object],
    ) -> SourceTaskCompletion:
        """Complete once; identical retries succeed without rewriting the result."""

        safe_result = _safe_json_object(result)
        with self._uow_factory() as uow:
            completion = uow.source_tasks.complete(
                task_id=task_id,
                lease_token=lease_token,
                result=safe_result,
                now=datetime.now(UTC),
            )
            uow.commit()
        return completion

    def snapshot(self, task_id: UUID) -> SourceTaskSnapshot:
        """Read task state without exposing lease or persistence details."""

        with self._uow_factory() as uow:
            return uow.source_tasks.get_snapshot(task_id)


def _safe_json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    """Validate JSON recursively and reject credential-shaped keys without echoing values."""

    _reject_credential_fields(value)
    result = _JSON_OBJECT.validate_python(_thaw(value), strict=True)
    return result


def _reject_credential_fields(value: object, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, MappingABC):
        for key, child in value.items():
            raw_key = str(key).casefold()
            normalized = re.sub(r"[^a-z0-9]", "", raw_key)
            segments = tuple(part for part in re.split(r"[^a-z0-9]+", raw_key) if part)
            sensitive = normalized.endswith(("authorization", "authorizations")) or (
                normalized.endswith(_CREDENTIAL_FIELD_SUFFIXES)
            )
            if segments and segments[-1] in _CREDENTIAL_FIELD_SUFFIXES:
                sensitive = True
            if sensitive:
                safe_path = ".".join((*path, str(key)))
                raise CredentialShapedPayloadError(
                    f"credential-shaped field is forbidden in source tasks: {safe_path}"
                )
            _reject_credential_fields(child, path=(*path, str(key)))
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_credential_fields(child, path=path)


def _thaw(value: object) -> object:
    if isinstance(value, MappingABC):
        return {str(key): _thaw(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(child) for child in value]
    return value


# Re-export the request from the service module as the application-facing task API.
__all__ = [
    "CredentialShapedPayloadError",
    "SourceTaskCompletionConflictError",
    "SourceTaskRequest",
    "SourceTaskService",
    "StaleSourceTaskLeaseError",
]
