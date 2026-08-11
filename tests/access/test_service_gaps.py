"""Coverage-gap tests for AccessService cleanup and broker edge paths."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.access.anonymous import (
    AnonymousAccessMethod,
    AnonymousProbeOutcome,
    AnonymousProbeResult,
)
from openbiliclaw.access.broker import AccessBroker, AccessUnavailableError
from openbiliclaw.access.forms import ConnectionForm, FieldKind, FormField
from openbiliclaw.access.manual import ManualAccessMethod, ManualProviderSpec
from openbiliclaw.access.methods import AccessMethodRegistry
from openbiliclaw.access.models import (
    AccessHandle,
    AccessMethodDescriptor,
    AccessRequest,
    CredentialAccessHandle,
    InteractionKind,
    Permission,
    VerificationResult,
    VerificationStrength,
)
from openbiliclaw.access.service import AccessService
from openbiliclaw.infrastructure.credentials.vault import CredentialVault

if TYPE_CHECKING:
    from collections.abc import Mapping


class MemoryBackend:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def get(self, secret_id: str) -> bytearray:
        if secret_id not in self.values:
            raise KeyError(secret_id)
        return bytearray(self.values[secret_id])

    def set(self, secret_id: str, secret: bytes) -> None:
        self.values[secret_id] = bytes(secret)

    def delete(self, secret_id: str) -> None:
        if secret_id not in self.values:
            raise KeyError(secret_id)
        del self.values[secret_id]


def _now() -> datetime:
    return datetime.now(UTC)


def _live_result() -> VerificationResult:
    return VerificationResult(
        strength=VerificationStrength.LIVE,
        verified_at=_now(),
        expires_at=_now() + timedelta(hours=1),
        granted_permissions=frozenset(
            {Permission.READ_PUBLIC, Permission.READ_PRIVATE, Permission.WRITE}
        ),
        safe_account_identity="public-account",
        sanitized_failure=None,
    )


def _manual_method(verifier: object) -> ManualAccessMethod:
    form = ConnectionForm(
        provider_id="github",
        method_id="builtin.manual",
        interaction=InteractionKind.SECRET_FORM,
        fields=(
            FormField(field_id="pat", label="PAT", kind=FieldKind.TOKEN, secret=True, min_length=8),
        ),
    )
    return ManualAccessMethod(
        CredentialVault(MemoryBackend()),
        (
            ManualProviderSpec(
                form=form,
                capabilities=frozenset(
                    {Permission.READ_PUBLIC, Permission.READ_PRIVATE, Permission.WRITE}
                ),
                verifier=verifier,  # type: ignore[arg-type]
            ),
        ),
    )


def _request(
    *permissions: Permission, methods: tuple[str, ...] = ("builtin.manual",)
) -> AccessRequest:
    return AccessRequest(
        provider_id="github",
        account_id="account-1",
        permissions=frozenset(permissions),
        supported_method_ids=methods,
    )


def _service(*methods: object) -> AccessService:
    registry = AccessMethodRegistry(methods)  # type: ignore[arg-type]
    return AccessService(AccessBroker(registry), registry, clock=_now)


class _ExplodingVerifyMethod:
    def __init__(self) -> None:
        self.closed: list[AccessHandle] = []

    descriptor = AccessMethodDescriptor(
        method_id="third.exploding",
        label="exploding",
        supported_provider_ids=frozenset({"github"}),
        interaction=InteractionKind.NONE,
        capabilities=frozenset({Permission.READ_PUBLIC}),
        supports_refresh=False,
    )

    def connection_form(self, provider_id: str) -> None:
        return None

    async def open(
        self, request: AccessRequest, submission: Mapping[str, str] | None
    ) -> AccessHandle:
        return CredentialAccessHandle(
            provider_id=request.provider_id,
            account_id=request.account_id,
            permissions=request.permissions,
            credential_ref="cred_" + "a" * 32,
            revision=1,
        )

    async def verify(self, handle: AccessHandle) -> VerificationResult:
        raise AccessUnavailableError("kaboom")

    async def refresh(self, handle: AccessHandle) -> AccessHandle:
        raise AccessUnavailableError("refresh_not_supported")

    async def close(self, handle: AccessHandle) -> None:
        self.closed.append(handle)


async def test_verify_raise_after_open_closes_handle() -> None:
    method = _ExplodingVerifyMethod()
    service = _service(method)
    with pytest.raises(AccessUnavailableError, match="kaboom"):
        await service.connect(
            _request(Permission.READ_PUBLIC, methods=("third.exploding",)),
            allowed_method_ids=frozenset({"third.exploding"}),
            submission=None,
        )
    assert len(method.closed) == 1


async def test_second_connect_raises_already_connected() -> None:
    method = _manual_method(ResultVerifierOk())
    service = _service(method)
    request = _request(Permission.READ_PRIVATE)
    await service.connect(
        request,
        allowed_method_ids=frozenset({"builtin.manual"}),
        submission={"pat": "x" * 8},
    )
    with pytest.raises(AccessUnavailableError, match="already_connected"):
        await service.connect(
            request,
            allowed_method_ids=frozenset({"builtin.manual"}),
            submission={"pat": "y" * 8},
        )


class ResultVerifierOk:
    async def __call__(
        self, handle: CredentialAccessHandle, credential: memoryview, /
    ) -> VerificationResult:
        return _live_result()


async def test_replace_on_non_replacing_method_raises_not_supported() -> None:
    anonymous = AnonymousAccessMethod(
        supported_providers=frozenset({"github"}),
        probe=_AvailableProbe(),
    )
    service = _service(anonymous)
    await service.connect(
        AccessRequest(
            provider_id="github",
            account_id=None,
            permissions=frozenset({Permission.READ_PUBLIC}),
            supported_method_ids=("builtin.anonymous",),
        ),
        allowed_method_ids=frozenset({"builtin.anonymous"}),
        submission=None,
    )
    with pytest.raises(AccessUnavailableError, match="replace_not_supported"):
        await service.replace(
            AccessRequest(
                provider_id="github",
                account_id=None,
                permissions=frozenset({Permission.READ_PUBLIC}),
                supported_method_ids=("builtin.anonymous",),
            ),
            {"pat": "x" * 8},
        )


class _AvailableProbe:
    async def __call__(self, provider_id: str) -> AnonymousProbeResult:
        return AnonymousProbeResult(outcome=AnonymousProbeOutcome.AVAILABLE)


async def test_broker_skips_unregistered_method_ids() -> None:
    method = _manual_method(ResultVerifierOk())
    registry = AccessMethodRegistry((method,))
    broker = AccessBroker(registry)
    request = _request(Permission.READ_PRIVATE, methods=("ghost.method", "builtin.manual"))
    opened = await broker.open(
        request,
        allowed_method_ids=frozenset({"ghost.method", "builtin.manual"}),
        submission={"pat": "x" * 8},
    )
    assert opened.method.descriptor.method_id == "builtin.manual"


class _WideningMethod:
    """Returns a handle whose permissions exceed the request."""

    def __init__(self) -> None:
        self.closed: list[AccessHandle] = []

    descriptor = AccessMethodDescriptor(
        method_id="third.widening",
        label="widening",
        supported_provider_ids=frozenset({"github"}),
        interaction=InteractionKind.NONE,
        capabilities=frozenset({Permission.READ_PUBLIC, Permission.WRITE}),
        supports_refresh=False,
    )

    def connection_form(self, provider_id: str) -> None:
        return None

    async def open(
        self, request: AccessRequest, submission: Mapping[str, str] | None
    ) -> AccessHandle:
        return CredentialAccessHandle(
            provider_id=request.provider_id,
            account_id=request.account_id,
            permissions=request.permissions | {Permission.WRITE},
            credential_ref="cred_" + "a" * 32,
            revision=1,
        )

    async def verify(self, handle: AccessHandle) -> VerificationResult:
        return _live_result()

    async def refresh(self, handle: AccessHandle) -> AccessHandle:
        raise AccessUnavailableError("refresh_not_supported")

    async def close(self, handle: AccessHandle) -> None:
        self.closed.append(handle)


async def test_broker_closes_scope_mismatched_handle_and_raises() -> None:
    method = _WideningMethod()
    registry = AccessMethodRegistry((method,))
    broker = AccessBroker(registry)
    with pytest.raises(AccessUnavailableError, match="method_returned_invalid_scope"):
        await broker.open(
            _request(Permission.READ_PUBLIC, methods=("third.widening",)),
            allowed_method_ids=frozenset({"third.widening"}),
            submission=None,
        )
    assert len(method.closed) == 1


class _WideningReplaceMethod(_WideningMethod):
    descriptor = AccessMethodDescriptor(
        method_id="third.widening",
        label="widening",
        supported_provider_ids=frozenset({"github"}),
        interaction=InteractionKind.NONE,
        capabilities=frozenset({Permission.READ_PUBLIC, Permission.WRITE}),
        supports_refresh=False,
    )

    async def open(
        self, request: AccessRequest, submission: Mapping[str, str] | None
    ) -> AccessHandle:
        return CredentialAccessHandle(
            provider_id=request.provider_id,
            account_id=request.account_id,
            permissions=request.permissions,
            credential_ref="cred_" + "a" * 32,
            revision=1,
        )

    async def replace(
        self, handle: AccessHandle, request: AccessRequest, submission: Mapping[str, str]
    ) -> AccessHandle:
        return CredentialAccessHandle(
            provider_id=request.provider_id,
            account_id=request.account_id,
            permissions=request.permissions | {Permission.WRITE},
            credential_ref="cred_" + "b" * 32,
            revision=2,
        )


async def test_replace_rejects_scope_widened_handle_and_closes_it() -> None:
    method = _WideningReplaceMethod()
    service = _service(method)
    request = _request(Permission.READ_PUBLIC, methods=("third.widening",))
    await service.connect(
        request, allowed_method_ids=frozenset({"third.widening"}), submission=None
    )
    with pytest.raises(AccessUnavailableError, match="method_returned_invalid_scope"):
        await service.replace(request, {"pat": "x" * 8})
    assert len(method.closed) == 1
