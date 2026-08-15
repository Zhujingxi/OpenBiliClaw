"""Manual credential acquisition behind the opaque CredentialVault boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from .broker import AccessUnavailableError
from .models import (
    AccessHandle,
    AccessMethodDescriptor,
    AccessRequest,
    CredentialAccessHandle,
    InteractionKind,
    Permission,
    ProviderId,
    VerificationFailure,
    VerificationResult,
    VerificationStrength,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.infrastructure.credentials.vault import CredentialVault

    from .forms import ConnectionForm, ValidatedSubmission


class CredentialVerifier(Protocol):
    """Trusted provider adapter invoked only inside a vault resolution scope."""

    async def __call__(
        self, handle: CredentialAccessHandle, credential: memoryview, /
    ) -> VerificationResult: ...


@dataclass(frozen=True, slots=True)
class ManualProviderSpec:
    """Provider-owned form, capabilities, and semantic verifier."""

    form: ConnectionForm
    capabilities: frozenset[Permission]
    verifier: CredentialVerifier

    def __post_init__(self) -> None:
        if self.form.method_id != "builtin.manual":
            raise ValueError("manual provider forms must use builtin.manual")
        if not self.capabilities:
            raise ValueError("manual provider capabilities cannot be empty")


class ManualAccessMethod:
    """Validate manual form values, vault them, and expose only opaque handles."""

    def __init__(self, vault: CredentialVault, specs: tuple[ManualProviderSpec, ...]) -> None:
        self._vault = vault
        self._specs = {spec.form.provider_id: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ValueError("duplicate manual provider specification")
        providers = frozenset(self._specs)
        capabilities = frozenset(permission for spec in specs for permission in spec.capabilities)
        self._descriptor = AccessMethodDescriptor(
            method_id="builtin.manual",
            label="Manually supplied credential",
            supported_provider_ids=providers,
            interaction=InteractionKind.SECRET_FORM,
            capabilities=capabilities,
            supports_refresh=False,
        )

    def __repr__(self) -> str:
        return f"ManualAccessMethod(providers={tuple(sorted(self._specs))!r}, vault=<redacted>)"

    @property
    def descriptor(self) -> AccessMethodDescriptor:
        return self._descriptor

    def connection_form(self, provider_id: ProviderId) -> ConnectionForm | None:
        spec = self._specs.get(provider_id)
        return spec.form if spec is not None else None

    async def open(
        self, request: AccessRequest, submission: Mapping[str, str] | None
    ) -> AccessHandle:
        spec = self._spec(request)
        if not request.permissions <= spec.capabilities:
            raise AccessUnavailableError("provider_permission_not_supported")
        if submission is None:
            raise AccessUnavailableError("manual_submission_required")
        validated = spec.form.validate_submission(submission)
        credential_ref = self._credential_ref(request.provider_id, request.account_id)
        self._vault.put(credential_ref, self._encode(validated))
        return CredentialAccessHandle(
            provider_id=request.provider_id,
            account_id=request.account_id,
            permissions=request.permissions,
            credential_ref=credential_ref,
            revision=1,
        )

    async def verify(self, handle: AccessHandle) -> VerificationResult:
        if not isinstance(handle, CredentialAccessHandle):
            raise AccessUnavailableError("wrong_handle_kind")
        spec = self._specs.get(handle.provider_id)
        if spec is None:
            raise AccessUnavailableError("provider_not_supported")

        async def verify_scoped(secret: memoryview) -> VerificationResult:
            return await spec.verifier(handle, secret)

        try:
            return await self._vault.resolve_async(handle.credential_ref, verify_scoped)
        except (KeyError, ValueError):
            return VerificationResult(
                strength=VerificationStrength.NONE,
                verified_at=datetime.now(UTC),
                sanitized_failure=VerificationFailure.INVALID_CREDENTIAL,
            )
        except Exception:
            return VerificationResult(
                strength=VerificationStrength.NONE,
                verified_at=datetime.now(UTC),
                sanitized_failure=VerificationFailure.NETWORK_UNAVAILABLE,
            )

    async def replace(
        self,
        handle: CredentialAccessHandle,
        request: AccessRequest,
        submission: Mapping[str, str],
    ) -> CredentialAccessHandle:
        if handle.provider_id != request.provider_id or handle.account_id != request.account_id:
            raise AccessUnavailableError("replacement_scope_mismatch")
        spec = self._spec(request)
        if not request.permissions <= spec.capabilities:
            raise AccessUnavailableError("provider_permission_not_supported")
        validated = spec.form.validate_submission(submission)
        self._vault.replace(handle.credential_ref, self._encode(validated))
        return CredentialAccessHandle(
            provider_id=request.provider_id,
            account_id=request.account_id,
            permissions=request.permissions,
            credential_ref=handle.credential_ref,
            revision=handle.revision + 1,
        )

    def permissions_for(self, provider_id: ProviderId) -> frozenset[Permission]:
        spec = self._specs.get(provider_id)
        if spec is None:
            raise AccessUnavailableError("provider_not_supported")
        return spec.capabilities

    def stored_handles(self) -> tuple[CredentialAccessHandle, ...]:
        """Reconstruct single-account handles whose secret slots survived restart."""

        handles: list[CredentialAccessHandle] = []
        for provider_id, spec in self._specs.items():
            credential_ref = self._credential_ref(provider_id, None)
            if self._vault.contains(credential_ref):
                handles.append(
                    CredentialAccessHandle(
                        provider_id=provider_id,
                        account_id=None,
                        permissions=spec.capabilities,
                        credential_ref=credential_ref,
                        revision=1,
                    )
                )
        return tuple(handles)

    async def refresh(self, handle: AccessHandle) -> AccessHandle:
        raise AccessUnavailableError("refresh_not_supported")

    async def close(self, handle: AccessHandle) -> None:
        if isinstance(handle, CredentialAccessHandle):
            self._vault.delete(handle.credential_ref)

    def _spec(self, request: AccessRequest) -> ManualProviderSpec:
        spec = self._specs.get(request.provider_id)
        if spec is None:
            raise AccessUnavailableError("provider_not_supported")
        return spec

    def _credential_ref(self, provider_id: str, account_id: str | None) -> str:
        account_scope = "none" if account_id is None else f"id:{account_id}"
        return self._vault.stable_reference(f"builtin.manual:{provider_id}:account:{account_scope}")

    @staticmethod
    def _encode(submission: ValidatedSubmission) -> bytes:
        return json.dumps(dict(submission.items()), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
