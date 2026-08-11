from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from openbiliclaw.access.anonymous import AnonymousAccessMethod, AnonymousProbeResult
from openbiliclaw.access.broker import AccessBroker, AccessUnavailableError
from openbiliclaw.access.forms import ConnectionForm, FieldKind, FormField
from openbiliclaw.access.manual import ManualAccessMethod, ManualProviderSpec
from openbiliclaw.access.methods import AccessMethodRegistry
from openbiliclaw.access.models import (
    AccessRequest,
    AnonymousAccessHandle,
    CredentialAccessHandle,
    InteractionKind,
    Permission,
    VerificationFailure,
    VerificationResult,
    VerificationStrength,
)
from openbiliclaw.access.service import AccessService
from openbiliclaw.infrastructure.credentials.vault import CredentialVault


class Backend:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def get(self, secret_id: str) -> bytearray:
        if secret_id not in self.values:
            raise KeyError(secret_id)
        return bytearray(self.values[secret_id])

    def set(self, secret_id: str, secret: bytes) -> None:
        self.values[secret_id] = secret

    def delete(self, secret_id: str) -> None:
        del self.values[secret_id]


def _request(
    provider: str = "x", permission: Permission = Permission.READ_PRIVATE
) -> AccessRequest:
    return AccessRequest(
        provider_id=provider,
        account_id="a",
        permissions=frozenset({permission}),
        supported_method_ids=("builtin.manual",),
    )


def _form(provider: str = "x", method: str = "builtin.manual") -> ConnectionForm:
    return ConnectionForm(
        provider_id=provider,
        method_id=method,
        interaction=InteractionKind.SECRET_FORM,
        fields=(
            FormField(
                field_id="value",
                label="Value",
                kind=FieldKind.TOKEN,
                secret=True,
            ),
        ),
    )


async def _success(handle: CredentialAccessHandle, credential: memoryview, /) -> VerificationResult:
    return VerificationResult(
        strength=VerificationStrength.LIVE,
        verified_at=datetime.now(UTC),
        granted_permissions=handle.permissions,
    )


def _manual(
    backend: Backend, *, capabilities: frozenset[Permission] = frozenset({Permission.READ_PRIVATE})
) -> ManualAccessMethod:
    return ManualAccessMethod(
        CredentialVault(backend),
        (ManualProviderSpec(form=_form(), capabilities=capabilities, verifier=_success),),
    )


async def test_anonymous_edge_contracts() -> None:
    async def failing_probe(_provider: str) -> AnonymousProbeResult:
        raise OSError

    method = AnonymousAccessMethod(
        supported_providers=frozenset({"x"}),
        probe=failing_probe,
    )
    assert method.connection_form("x") is None
    request = AccessRequest(
        provider_id="x",
        permissions=frozenset({Permission.READ_PUBLIC}),
        supported_method_ids=("builtin.anonymous",),
    )
    handle = await method.open(request, None)
    assert (
        await method.verify(handle)
    ).sanitized_failure is VerificationFailure.NETWORK_UNAVAILABLE
    credential = CredentialAccessHandle(
        provider_id="x",
        account_id=None,
        permissions=frozenset({Permission.READ_PUBLIC}),
        credential_ref="cred_" + "a" * 32,
        revision=1,
    )
    with pytest.raises(AccessUnavailableError, match="wrong_handle_kind"):
        await method.verify(credential)
    with pytest.raises(AccessUnavailableError, match="refresh_not_supported"):
        await method.refresh(handle)
    await method.close(handle)
    with pytest.raises(AccessUnavailableError, match="anonymous_does_not_accept_submission"):
        await method.open(request, {"unexpected": "value"})


async def test_manual_edge_contracts_and_safe_verifier_errors() -> None:
    backend = Backend()
    method = _manual(backend)
    assert "vault=<redacted>" in repr(method)
    assert method.connection_form("x") is not None
    assert method.connection_form("other") is None
    with pytest.raises(AccessUnavailableError, match="manual_submission_required"):
        await method.open(_request(), None)
    with pytest.raises(AccessUnavailableError, match="provider_not_supported"):
        await method.open(_request("other"), {"value": "x"})
    with pytest.raises(AccessUnavailableError, match="provider_permission_not_supported"):
        await method.open(_request(permission=Permission.WRITE), {"value": "x"})

    anonymous = AnonymousAccessHandle(
        provider_id="x", account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )
    with pytest.raises(AccessUnavailableError, match="wrong_handle_kind"):
        await method.verify(anonymous)
    with pytest.raises(AccessUnavailableError, match="refresh_not_supported"):
        await method.refresh(anonymous)
    await method.close(anonymous)

    missing = CredentialAccessHandle(
        provider_id="x",
        account_id="a",
        permissions=frozenset({Permission.READ_PRIVATE}),
        credential_ref="cred_" + "b" * 32,
        revision=1,
    )
    assert (
        await method.verify(missing)
    ).sanitized_failure is VerificationFailure.INVALID_CREDENTIAL

    async def broken_verifier(
        _handle: CredentialAccessHandle, _credential: memoryview, /
    ) -> VerificationResult:
        raise RuntimeError

    broken = ManualAccessMethod(
        CredentialVault(backend),
        (
            ManualProviderSpec(
                form=_form(),
                capabilities=frozenset({Permission.READ_PRIVATE}),
                verifier=broken_verifier,
            ),
        ),
    )
    opened = await broken.open(_request(), {"value": "x"})
    assert (
        await broken.verify(opened)
    ).sanitized_failure is VerificationFailure.NETWORK_UNAVAILABLE


async def test_manual_spec_replace_and_registry_invariants() -> None:
    backend = Backend()
    with pytest.raises(ValueError, match="builtin.manual"):
        ManualProviderSpec(
            form=_form(method="custom.manual"),
            capabilities=frozenset({Permission.READ_PRIVATE}),
            verifier=_success,
        )
    with pytest.raises(ValueError, match="cannot be empty"):
        ManualProviderSpec(form=_form(), capabilities=frozenset(), verifier=_success)
    spec = ManualProviderSpec(
        form=_form(), capabilities=frozenset({Permission.READ_PRIVATE}), verifier=_success
    )
    with pytest.raises(ValueError, match="duplicate"):
        ManualAccessMethod(CredentialVault(backend), (spec, spec))
    method = ManualAccessMethod(CredentialVault(backend), (spec,))
    handle = await method.open(_request(), {"value": "x"})
    assert isinstance(handle, CredentialAccessHandle)
    with pytest.raises(AccessUnavailableError, match="replacement_scope_mismatch"):
        await method.replace(handle, _request(provider="other"), {"value": "x"})

    registry = AccessMethodRegistry((method,))
    assert registry.get("missing") is None
    assert len(registry.methods) == 1
    service = AccessService(AccessBroker(registry), registry)
    assert service.connection_forms("x") == (_form(),)
    assert service.connected_handle("x", "a") is None
    assert (await service.status("x", "a")).state.value == "disconnected"
    with pytest.raises(AccessUnavailableError, match="not_connected"):
        await service.replace(_request(), {"value": "x"})
    with pytest.raises(ValueError, match="positive"):
        AccessService(AccessBroker(registry), registry, verification_ttl=timedelta(0))
