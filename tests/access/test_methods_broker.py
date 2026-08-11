from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping

from openbiliclaw.access.anonymous import (
    AnonymousAccessMethod,
    AnonymousProbeOutcome,
    AnonymousProbeResult,
)
from openbiliclaw.access.broker import AccessBroker, AccessUnavailableError
from openbiliclaw.access.methods import AccessMethod, AccessMethodRegistry
from openbiliclaw.access.models import (
    AccessHandle,
    AccessMethodDescriptor,
    AccessRequest,
    AnonymousAccessHandle,
    CredentialAccessHandle,
    InteractionKind,
    Permission,
    VerificationFailure,
    VerificationResult,
)


class FixedProbe:
    def __init__(self, outcome: AnonymousProbeOutcome) -> None:
        self._outcome = outcome

    async def __call__(self, provider_id: str) -> AnonymousProbeResult:
        return AnonymousProbeResult(outcome=self._outcome)


@pytest.mark.parametrize(
    ("outcome", "failure"),
    [
        (AnonymousProbeOutcome.AVAILABLE, None),
        (AnonymousProbeOutcome.RATE_LIMITED, VerificationFailure.RATE_LIMITED),
        (AnonymousProbeOutcome.GEO_BLOCKED, VerificationFailure.GEO_BLOCKED),
        (AnonymousProbeOutcome.NETWORK_UNAVAILABLE, VerificationFailure.NETWORK_UNAVAILABLE),
    ],
)
async def test_anonymous_verification_has_no_invented_identity(
    outcome: AnonymousProbeOutcome, failure: VerificationFailure | None
) -> None:
    method = AnonymousAccessMethod(
        supported_providers=frozenset({"bilibili"}),
        probe=FixedProbe(outcome),
    )
    request = AccessRequest(
        provider_id="bilibili",
        account_id=None,
        permissions=frozenset({Permission.READ_PUBLIC}),
        supported_method_ids=("builtin.anonymous",),
    )
    handle = await method.open(request, None)
    result = await method.verify(handle)
    assert result.safe_account_identity is None
    assert result.sanitized_failure is failure
    assert result.verified_at.tzinfo is not None


async def test_anonymous_rejects_write_and_wrong_provider() -> None:
    method = AnonymousAccessMethod(
        supported_providers=frozenset({"bilibili"}),
        probe=FixedProbe(AnonymousProbeOutcome.AVAILABLE),
    )
    for request in (
        AccessRequest(
            provider_id="bilibili",
            permissions=frozenset({Permission.WRITE}),
            supported_method_ids=("builtin.anonymous",),
        ),
        AccessRequest(
            provider_id="github",
            permissions=frozenset({Permission.READ_PUBLIC}),
            supported_method_ids=("builtin.anonymous",),
        ),
    ):
        with pytest.raises(AccessUnavailableError):
            await AccessBroker(AccessMethodRegistry((method,))).open(
                request, allowed_method_ids=frozenset({"builtin.anonymous"}), submission=None
            )


def _method(
    method_id: str, providers: frozenset[str], permissions: frozenset[Permission]
) -> AccessMethod:
    class Stub:
        descriptor = AccessMethodDescriptor(
            method_id=method_id,
            label=method_id,
            supported_provider_ids=providers,
            interaction=InteractionKind.NONE,
            capabilities=permissions,
            supports_refresh=False,
        )

        def connection_form(self, provider_id: str) -> None:
            return None

        async def open(
            self, request: AccessRequest, submission: Mapping[str, str] | None
        ) -> AccessHandle:
            if request.permissions <= {Permission.READ_PUBLIC}:
                return AnonymousAccessHandle(
                    provider_id=request.provider_id,
                    account_id=request.account_id,
                    permissions=request.permissions,
                )
            return CredentialAccessHandle(
                provider_id=request.provider_id,
                account_id=request.account_id,
                permissions=request.permissions,
                credential_ref="cred_" + "a" * 32,
                revision=1,
            )

        async def verify(self, handle: AccessHandle) -> VerificationResult:
            raise AssertionError("not used")

        async def refresh(self, handle: AccessHandle) -> AccessHandle:
            raise AccessUnavailableError("refresh_not_supported")

        async def close(self, handle: AccessHandle) -> None:
            return None

    return Stub()


@pytest.mark.parametrize(
    ("supported", "allowed", "provider", "permission", "selected"),
    [
        (
            ("third.manual", "builtin.anonymous"),
            {"third.manual"},
            "x",
            Permission.WRITE,
            "third.manual",
        ),
        (
            ("builtin.anonymous", "third.manual"),
            {"builtin.anonymous", "third.manual"},
            "x",
            Permission.READ_PUBLIC,
            "builtin.anonymous",
        ),
        (("third.manual",), {"third.manual"}, "other", Permission.WRITE, None),
        (("builtin.anonymous",), {"builtin.anonymous"}, "x", Permission.WRITE, None),
        (("third.manual",), set(), "x", Permission.WRITE, None),
    ],
)
async def test_broker_selection_and_permission_narrowing(
    supported: tuple[str, ...],
    allowed: set[str],
    provider: str,
    permission: Permission,
    selected: str | None,
) -> None:
    anonymous = _method("builtin.anonymous", frozenset({"x"}), frozenset({Permission.READ_PUBLIC}))
    manual = _method(
        "third.manual",
        frozenset({"x"}),
        frozenset({Permission.READ_PUBLIC, Permission.WRITE}),
    )
    broker = AccessBroker(AccessMethodRegistry((anonymous, manual)))
    request = AccessRequest(
        provider_id=provider,
        permissions=frozenset({permission}),
        supported_method_ids=supported,
    )
    if selected is None:
        with pytest.raises(AccessUnavailableError, match="no_allowed_method"):
            await broker.open(request, allowed_method_ids=frozenset(allowed), submission=None)
    else:
        opened = await broker.open(request, allowed_method_ids=frozenset(allowed), submission=None)
        assert opened.method.descriptor.method_id == selected
        assert opened.handle.permissions == request.permissions


def test_registry_rejects_duplicate_and_exposes_core_registration() -> None:
    method = _method("third.manual", frozenset({"x"}), frozenset({Permission.WRITE}))
    registry = AccessMethodRegistry((method,))
    assert registry.extension_registrations[0].extension_id == "third.manual"
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(method)
