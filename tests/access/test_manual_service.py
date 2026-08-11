from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from openbiliclaw.access.broker import AccessBroker
from openbiliclaw.access.forms import ConnectionForm, FieldKind, FormField
from openbiliclaw.access.manual import ManualAccessMethod, ManualProviderSpec
from openbiliclaw.access.methods import AccessMethodRegistry
from openbiliclaw.access.models import (
    AccessRequest,
    AccessStatusKind,
    CredentialAccessHandle,
    InteractionKind,
    Permission,
    VerificationFailure,
    VerificationResult,
    VerificationStrength,
)
from openbiliclaw.access.service import AccessService
from openbiliclaw.infrastructure.credentials.vault import CredentialVault
from openbiliclaw.infrastructure.telemetry import TelemetrySink


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


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2030, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class Verifier(Protocol):
    async def __call__(
        self, handle: CredentialAccessHandle, credential: memoryview, /
    ) -> VerificationResult: ...


class ResultVerifier:
    def __init__(self, result: Callable[[], VerificationResult]) -> None:
        self._result = result

    async def __call__(
        self, handle: CredentialAccessHandle, credential: memoryview, /
    ) -> VerificationResult:
        return self._result()


def _form() -> ConnectionForm:
    return ConnectionForm(
        provider_id="github",
        method_id="builtin.manual",
        interaction=InteractionKind.SECRET_FORM,
        fields=(
            FormField(
                field_id="pat",
                label="PAT",
                kind=FieldKind.TOKEN,
                secret=True,
                min_length=8,
            ),
        ),
    )


def _request(*permissions: Permission) -> AccessRequest:
    return AccessRequest(
        provider_id="github",
        account_id="account-1",
        permissions=frozenset(permissions),
        supported_method_ids=("builtin.manual",),
    )


def _service(
    backend: MemoryBackend,
    verifier: Verifier,
    clock: Clock,
    telemetry: TelemetrySink | None = None,
) -> tuple[AccessService, ManualAccessMethod]:
    method = ManualAccessMethod(
        CredentialVault(backend),
        (
            ManualProviderSpec(
                form=_form(),
                capabilities=frozenset(
                    {Permission.READ_PUBLIC, Permission.READ_PRIVATE, Permission.WRITE}
                ),
                verifier=verifier,
            ),
        ),
    )
    registry = AccessMethodRegistry((method,))
    return (
        AccessService(
            AccessBroker(registry),
            registry,
            verification_ttl=timedelta(minutes=5),
            clock=clock,
            telemetry=telemetry,
        ),
        method,
    )


def _result(
    clock: Clock,
    *,
    failure: VerificationFailure | None = None,
    granted: frozenset[Permission] = frozenset({Permission.READ_PRIVATE}),
    expires_in: timedelta | None = timedelta(hours=1),
) -> VerificationResult:
    return VerificationResult(
        strength=VerificationStrength.LIVE,
        verified_at=clock(),
        expires_at=clock() + expires_in if expires_in is not None else None,
        granted_permissions=granted,
        safe_account_identity="public-account" if failure is None else None,
        sanitized_failure=failure,
    )


async def test_manual_secret_goes_straight_to_vault_and_never_serializes() -> None:
    backend = MemoryBackend()
    clock = Clock()
    seen: list[dict[str, str]] = []

    async def verifier(
        handle: CredentialAccessHandle, credential: memoryview, /
    ) -> VerificationResult:
        seen.append(json.loads(credential.tobytes()))
        return _result(clock)

    canary = "".join(chr(code) for code in (67, 65, 78, 65, 82, 89, 45, 49))
    telemetry = TelemetrySink(secret_values=(canary,))
    service, _method = _service(backend, verifier, clock, telemetry)
    status = await service.connect(
        _request(Permission.READ_PRIVATE),
        allowed_method_ids=frozenset({"builtin.manual"}),
        submission={"pat": canary},
    )
    assert seen == [{"pat": canary}]
    assert status.state is AccessStatusKind.CONNECTED
    assert canary not in status.model_dump_json()
    assert canary not in repr(service)
    assert canary not in repr(telemetry.records)
    assert len(backend.values) == 1
    stored = next(iter(backend.values.values()))
    assert canary.encode() in stored


@pytest.mark.parametrize(
    ("failure", "state"),
    [
        (VerificationFailure.INVALID_CREDENTIAL, AccessStatusKind.UNVERIFIED),
        (VerificationFailure.EXPIRED, AccessStatusKind.EXPIRED),
        (VerificationFailure.INSUFFICIENT_SCOPE, AccessStatusKind.DEGRADED),
        (VerificationFailure.RATE_LIMITED, AccessStatusKind.DEGRADED),
        (VerificationFailure.GEO_BLOCKED, AccessStatusKind.UNAVAILABLE),
        (VerificationFailure.NETWORK_UNAVAILABLE, AccessStatusKind.UNAVAILABLE),
    ],
)
async def test_verification_failures_map_to_safe_status(
    failure: VerificationFailure, state: AccessStatusKind
) -> None:
    backend = MemoryBackend()
    clock = Clock()
    service, _method = _service(
        backend, ResultVerifier(lambda: _result(clock, failure=failure)), clock
    )
    status = await service.connect(
        _request(Permission.READ_PRIVATE),
        allowed_method_ids=frozenset({"builtin.manual"}),
        submission={"pat": "x" * 8},
    )
    assert status.state is state
    assert status.verification is not None
    assert status.verification.sanitized_failure is failure


async def test_insufficient_granted_scope_is_degraded_even_if_verifier_succeeds() -> None:
    backend = MemoryBackend()
    clock = Clock()
    service, _method = _service(
        backend,
        ResultVerifier(lambda: _result(clock, granted=frozenset({Permission.READ_PUBLIC}))),
        clock,
    )
    status = await service.connect(
        _request(Permission.WRITE),
        allowed_method_ids=frozenset({"builtin.manual"}),
        submission={"pat": "x" * 8},
    )
    assert status.state is AccessStatusKind.DEGRADED
    assert status.verification is not None
    assert status.verification.sanitized_failure is VerificationFailure.INSUFFICIENT_SCOPE


async def test_verification_cache_expires_and_replace_invalidates_it() -> None:
    backend = MemoryBackend()
    clock = Clock()
    calls = 0

    async def verifier(
        _handle: CredentialAccessHandle, _credential: memoryview, /
    ) -> VerificationResult:
        nonlocal calls
        calls += 1
        return _result(clock)

    service, _method = _service(backend, verifier, clock)
    request = _request(Permission.READ_PRIVATE)
    await service.connect(
        request,
        allowed_method_ids=frozenset({"builtin.manual"}),
        submission={"pat": "a" * 8},
    )
    await service.status("github", "account-1")
    assert calls == 1
    clock.now += timedelta(minutes=6)
    await service.status("github", "account-1")
    assert calls == 2
    await service.replace(request, {"pat": "b" * 8})
    assert calls == 3
    handle = service.connected_handle("github", "account-1")
    assert isinstance(handle, CredentialAccessHandle)
    assert handle.revision == 2
    assert len(backend.values) == 1


async def test_disconnect_revokes_secret_and_is_idempotent() -> None:
    backend = MemoryBackend()
    clock = Clock()
    service, _method = _service(backend, ResultVerifier(lambda: _result(clock)), clock)
    await service.connect(
        _request(Permission.READ_PRIVATE),
        allowed_method_ids=frozenset({"builtin.manual"}),
        submission={"pat": "x" * 8},
    )
    assert backend.values
    disconnected = await service.disconnect("github", "account-1")
    assert disconnected.state is AccessStatusKind.DISCONNECTED
    assert backend.values == {}
    assert (await service.disconnect("github", "account-1")).state is AccessStatusKind.DISCONNECTED


async def test_malformed_form_fails_before_vault_write_and_error_is_redacted() -> None:
    backend = MemoryBackend()
    clock = Clock()
    service, _method = _service(backend, ResultVerifier(lambda: _result(clock)), clock)
    canary = "".join(chr(code) for code in (98, 97, 100))
    with pytest.raises(ValueError) as exc:
        await service.connect(
            _request(Permission.READ_PRIVATE),
            allowed_method_ids=frozenset({"builtin.manual"}),
            submission={"pat": canary},
        )
    assert canary not in str(exc.value)
    assert backend.values == {}
