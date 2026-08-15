"""Provider Access connect/status/replace/disconnect use cases."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from .broker import AccessBroker, AccessUnavailableError
from .methods import (
    AccessMethod,
    AccessMethodRegistry,
    ProviderScopedAccessMethod,
    RehydratingAccessMethod,
    ReplacingAccessMethod,
)
from .models import AccessHandle, AccessRequest, AccessStatus, AccessStatusKind, Permission
from .verification import cache_is_valid, enforce_requested_permissions, project_status

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from openbiliclaw.infrastructure.telemetry import TelemetrySink

    from .forms import ConnectionForm


@dataclass(slots=True)
class _Connection:
    method: AccessMethod
    handle: AccessHandle
    status: AccessStatus


class AccessService:
    """Serialize connection lifecycle and cache only bounded verification evidence."""

    def __init__(
        self,
        broker: AccessBroker,
        registry: AccessMethodRegistry,
        *,
        verification_ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] | None = None,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        if verification_ttl <= timedelta(0):
            raise ValueError("verification_ttl must be positive")
        self._broker = broker
        self._registry = registry
        self._verification_ttl = verification_ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._telemetry = telemetry
        self._connections: dict[tuple[str, str | None], _Connection] = {}
        # ponytail: one lock serializes rare credential mutations; split per account
        # if connect throughput ever matters.
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"AccessService(connections={len(self._connections)}, secrets=<inaccessible>)"

    async def connect(
        self,
        request: AccessRequest,
        *,
        allowed_method_ids: frozenset[str],
        submission: Mapping[str, str] | None,
    ) -> AccessStatus:
        key = self._key(request.provider_id, request.account_id)
        async with self._lock:
            if key in self._connections:
                raise AccessUnavailableError("already_connected")
            with self._trace("access.connect", request.provider_id):
                opened = await self._broker.open(
                    request,
                    allowed_method_ids=allowed_method_ids,
                    submission=submission,
                )
                try:
                    result = enforce_requested_permissions(
                        await opened.method.verify(opened.handle), request.permissions
                    )
                    status = project_status(
                        opened.handle,
                        opened.method.descriptor.method_id,
                        result,
                        now=self._clock(),
                    )
                except BaseException:
                    await opened.method.close(opened.handle)
                    raise
                self._connections[key] = _Connection(opened.method, opened.handle, status)
                return status

    async def rehydrate(self) -> None:
        """Idempotently verify and restore durable single-account connections."""

        async with self._lock:
            for method in self._registry.methods:
                if not isinstance(method, RehydratingAccessMethod):
                    continue
                for handle in method.stored_handles():
                    key = self._key(handle.provider_id, handle.account_id)
                    if key in self._connections:
                        continue
                    result = enforce_requested_permissions(
                        await method.verify(handle), handle.permissions
                    )
                    self._connections[key] = _Connection(
                        method,
                        handle,
                        project_status(
                            handle,
                            method.descriptor.method_id,
                            result,
                            now=self._clock(),
                        ),
                    )

    def method_permissions(self, provider_id: str, method_id: str) -> frozenset[Permission]:
        """Return declared permissions for one provider-supported method."""

        method = self._registry.get(method_id)
        if method is None or provider_id not in method.descriptor.supported_provider_ids:
            raise AccessUnavailableError("provider_not_supported")
        if isinstance(method, ProviderScopedAccessMethod):
            return method.permissions_for(provider_id)
        return frozenset(method.descriptor.capabilities)

    async def status(self, provider_id: str, account_id: str | None) -> AccessStatus:
        key = self._key(provider_id, account_id)
        async with self._lock:
            connection = self._connections.get(key)
            if connection is None:
                return self._disconnected(provider_id, account_id)
            evidence = connection.status.verification
            if evidence is not None and cache_is_valid(
                evidence, now=self._clock(), maximum_age=self._verification_ttl
            ):
                return connection.status
            result = enforce_requested_permissions(
                await connection.method.verify(connection.handle), connection.handle.permissions
            )
            connection.status = project_status(
                connection.handle,
                connection.method.descriptor.method_id,
                result,
                now=self._clock(),
            )
            return connection.status

    async def replace(self, request: AccessRequest, submission: Mapping[str, str]) -> AccessStatus:
        key = self._key(request.provider_id, request.account_id)
        async with self._lock:
            connection = self._connections.get(key)
            if connection is None:
                raise AccessUnavailableError("not_connected")
            method = connection.method
            if not isinstance(method, ReplacingAccessMethod):
                raise AccessUnavailableError("replace_not_supported")
            with self._trace("access.replace", request.provider_id):
                handle = await method.replace(connection.handle, request, submission)
                # A third-party method could silently widen scope; enforce the
                # same equality check the broker applies on open.
                if (
                    handle.provider_id != request.provider_id
                    or handle.account_id != request.account_id
                    or handle.permissions != request.permissions
                ):
                    await method.close(handle)
                    raise AccessUnavailableError("method_returned_invalid_scope")
                result = enforce_requested_permissions(
                    await method.verify(handle), request.permissions
                )
                status = project_status(
                    handle, method.descriptor.method_id, result, now=self._clock()
                )
                connection.handle = handle
                connection.status = status
                return status

    async def disconnect(self, provider_id: str, account_id: str | None) -> AccessStatus:
        key = self._key(provider_id, account_id)
        async with self._lock:
            connection = self._connections.get(key)
            if connection is None:
                return self._disconnected(provider_id, account_id)
            with self._trace("access.disconnect", provider_id):
                await connection.method.close(connection.handle)
                del self._connections[key]
            return self._disconnected(provider_id, account_id)

    def connection_forms(self, provider_id: str) -> tuple[ConnectionForm, ...]:
        return tuple(
            form
            for method in self._registry.methods
            if (form := method.connection_form(provider_id)) is not None
        )

    def connected_handle(self, provider_id: str, account_id: str | None) -> AccessHandle | None:
        connection = self._connections.get(self._key(provider_id, account_id))
        return connection.handle if connection is not None else None

    @staticmethod
    def _key(provider_id: str, account_id: str | None) -> tuple[str, str | None]:
        return provider_id, account_id

    @staticmethod
    def _disconnected(provider_id: str, account_id: str | None) -> AccessStatus:
        return AccessStatus(
            provider_id=provider_id,
            account_id=account_id,
            state=AccessStatusKind.DISCONNECTED,
        )

    def _trace(self, name: str, provider_id: str) -> AbstractContextManager[object]:
        if self._telemetry is None:
            return nullcontext()
        return self._telemetry.trace(name, {"provider_id": provider_id})
