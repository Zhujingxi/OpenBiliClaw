"""Public-read-only anonymous access method."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from .broker import AccessUnavailableError
from .models import (
    AccessHandle,
    AccessMethodDescriptor,
    AccessRequest,
    AnonymousAccessHandle,
    InteractionKind,
    Permission,
    ProviderId,
    VerificationFailure,
    VerificationResult,
    VerificationStrength,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .forms import ConnectionForm


class AnonymousProbeOutcome(StrEnum):
    AVAILABLE = "available"
    RATE_LIMITED = "rate_limited"
    GEO_BLOCKED = "geo_blocked"
    NETWORK_UNAVAILABLE = "network_unavailable"


class AnonymousProbeResult:
    """Provider-safe public-endpoint outcome with no response body."""

    __slots__ = ("outcome",)

    def __init__(self, *, outcome: AnonymousProbeOutcome) -> None:
        self.outcome = outcome


class AnonymousProbe(Protocol):
    async def __call__(self, provider_id: str, /) -> AnonymousProbeResult: ...


_FAILURES = {
    AnonymousProbeOutcome.RATE_LIMITED: VerificationFailure.RATE_LIMITED,
    AnonymousProbeOutcome.GEO_BLOCKED: VerificationFailure.GEO_BLOCKED,
    AnonymousProbeOutcome.NETWORK_UNAVAILABLE: VerificationFailure.NETWORK_UNAVAILABLE,
}


class AnonymousAccessMethod:
    """Acquire an identity-free handle for public read operations."""

    def __init__(self, *, supported_providers: frozenset[str], probe: AnonymousProbe) -> None:
        self._probe = probe
        self._descriptor = AccessMethodDescriptor(
            method_id="builtin.anonymous",
            label="Anonymous public access",
            supported_provider_ids=supported_providers,
            interaction=InteractionKind.NONE,
            capabilities=frozenset({Permission.READ_PUBLIC}),
            supports_refresh=False,
        )

    @property
    def descriptor(self) -> AccessMethodDescriptor:
        return self._descriptor

    def connection_form(self, provider_id: ProviderId) -> ConnectionForm | None:
        return None

    async def open(
        self, request: AccessRequest, submission: Mapping[str, str] | None
    ) -> AccessHandle:
        if submission:
            raise AccessUnavailableError("anonymous_does_not_accept_submission")
        if request.provider_id not in self.descriptor.supported_provider_ids:
            raise AccessUnavailableError("provider_not_supported")
        if request.account_id is not None or not request.permissions <= {Permission.READ_PUBLIC}:
            raise AccessUnavailableError("anonymous_scope_not_supported")
        return AnonymousAccessHandle(
            provider_id=request.provider_id,
            account_id=None,
            permissions=request.permissions,
        )

    async def verify(self, handle: AccessHandle) -> VerificationResult:
        if not isinstance(handle, AnonymousAccessHandle):
            raise AccessUnavailableError("wrong_handle_kind")
        try:
            outcome = (await self._probe(handle.provider_id)).outcome
        except Exception:
            outcome = AnonymousProbeOutcome.NETWORK_UNAVAILABLE
        failure = _FAILURES.get(outcome)
        return VerificationResult(
            strength=VerificationStrength.LIVE,
            verified_at=datetime.now(UTC),
            granted_permissions=(
                handle.permissions if outcome is AnonymousProbeOutcome.AVAILABLE else frozenset()
            ),
            sanitized_failure=failure,
        )

    async def refresh(self, handle: AccessHandle) -> AccessHandle:
        raise AccessUnavailableError("refresh_not_supported")

    async def close(self, handle: AccessHandle) -> None:
        return None
