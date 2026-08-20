"""Narrow AccessMethod extension contract and typed registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from openbiliclaw.core.extensions import AccessMethodRegistration

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .forms import ConnectionForm
    from .models import (
        AccessHandle,
        AccessMethodDescriptor,
        AccessRequest,
        Permission,
        ProviderId,
        VerificationResult,
    )


class AccessMethod(Protocol):
    """Acquire and manage opaque access for one or more providers."""

    @property
    def descriptor(self) -> AccessMethodDescriptor: ...

    def connection_form(self, provider_id: ProviderId) -> ConnectionForm | None: ...

    async def open(
        self, request: AccessRequest, submission: Mapping[str, str] | None
    ) -> AccessHandle: ...

    async def verify(self, handle: AccessHandle) -> VerificationResult: ...

    async def refresh(self, handle: AccessHandle) -> AccessHandle: ...

    async def close(self, handle: AccessHandle) -> None: ...


@runtime_checkable
class ReplacingAccessMethod(AccessMethod, Protocol):
    async def replace(
        self,
        handle: AccessHandle,
        request: AccessRequest,
        submission: Mapping[str, str],
    ) -> AccessHandle: ...


@runtime_checkable
class ProviderScopedAccessMethod(AccessMethod, Protocol):
    def permissions_for(self, provider_id: ProviderId) -> frozenset[Permission]: ...


@runtime_checkable
class RehydratingAccessMethod(AccessMethod, Protocol):
    """Method that can reconstruct handles from durable opaque slots."""

    def stored_handles(self) -> tuple[AccessHandle, ...]: ...


@runtime_checkable
class ReplayingAccessMethod(AccessMethod, Protocol):
    """Method that can recognize an exact submission for a restored handle."""

    def matches_replay(
        self,
        handle: AccessHandle,
        request: AccessRequest,
        submission: Mapping[str, str] | None,
    ) -> bool: ...


class AccessMethodRegistry:
    """Access-method registry; Core registration remains metadata, not service location."""

    def __init__(self, methods: tuple[AccessMethod, ...] = ()) -> None:
        self._methods: dict[str, AccessMethod] = {}
        for method in methods:
            self.register(method)

    def register(self, method: AccessMethod) -> None:
        method_id = method.descriptor.method_id
        if method_id in self._methods:
            raise ValueError(f"duplicate access method: {method_id}")
        self._methods[method_id] = method

    def get(self, method_id: str) -> AccessMethod | None:
        return self._methods.get(method_id)

    @property
    def methods(self) -> tuple[AccessMethod, ...]:
        return tuple(self._methods.values())

    @property
    def extension_registrations(self) -> tuple[AccessMethodRegistration, ...]:
        return tuple(
            AccessMethodRegistration(
                extension_id=method.descriptor.method_id,
                capability_version=1,
            )
            for method in self._methods.values()
        )
