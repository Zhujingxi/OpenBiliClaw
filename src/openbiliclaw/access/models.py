"""Secret-free Provider Access value objects and transport-safe models."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from openbiliclaw.core._pydantic import StrictBaseModel

ProviderId: TypeAlias = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
AccountId: TypeAlias = Annotated[str, Field(min_length=1, max_length=128)]
MethodId: TypeAlias = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")]
CredentialRef: TypeAlias = Annotated[str, Field(pattern=r"^cred_[0-9a-f]{32}$")]


class Permission(StrEnum):
    READ_PUBLIC = "read_public"
    READ_PRIVATE = "read_private"
    WRITE = "write"


class InteractionKind(StrEnum):
    NONE = "none"
    SECRET_FORM = "secret_form"


class AccessStatusKind(StrEnum):
    DISCONNECTED = "disconnected"
    UNVERIFIED = "unverified"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


class VerificationStrength(StrEnum):
    NONE = "none"
    LOCAL = "local"
    LIVE = "live"


class VerificationFailure(StrEnum):
    INVALID_CREDENTIAL = "invalid_credential"
    EXPIRED = "expired"
    INSUFFICIENT_SCOPE = "insufficient_scope"
    RATE_LIMITED = "rate_limited"
    GEO_BLOCKED = "geo_blocked"
    NETWORK_UNAVAILABLE = "network_unavailable"
    SESSION_MODE_UNSUPPORTED = "session_mode_unsupported"


class AccessMethodDescriptor(StrictBaseModel):
    """Safe description of one concrete credential-acquisition method."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method_id: MethodId
    label: str = Field(min_length=1, max_length=80)
    supported_provider_ids: frozenset[ProviderId]
    interaction: InteractionKind
    capabilities: frozenset[Permission]
    supports_refresh: bool


class AccessRequest(StrictBaseModel):
    """Provider/account scope requested by a content adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    account_id: AccountId | None = None
    permissions: frozenset[Permission]
    supported_method_ids: tuple[MethodId, ...]

    @model_validator(mode="after")
    def _not_empty(self) -> AccessRequest:
        if not self.permissions:
            raise ValueError("at least one permission is required")
        if not self.supported_method_ids:
            raise ValueError("at least one supported method is required")
        return self


class AnonymousAccessHandle(StrictBaseModel):
    """Public-only handle; it has no credential storage reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["anonymous"] = "anonymous"
    provider_id: ProviderId
    account_id: AccountId | None
    permissions: frozenset[Permission]

    @model_validator(mode="after")
    def _public_only(self) -> AnonymousAccessHandle:
        if not self.permissions <= {Permission.READ_PUBLIC}:
            raise ValueError("anonymous handles allow public reads only")
        if self.account_id is not None:
            raise ValueError("anonymous handles cannot claim an account identity")
        return self


class CredentialAccessHandle(StrictBaseModel):
    """Opaque credential reference plus immutable provider/account scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["credential"] = "credential"
    provider_id: ProviderId
    account_id: AccountId | None
    permissions: frozenset[Permission]
    credential_ref: CredentialRef
    revision: int = Field(ge=1)


AccessHandle: TypeAlias = Annotated[
    AnonymousAccessHandle | CredentialAccessHandle, Field(discriminator="kind")
]


class VerificationResult(StrictBaseModel):
    """Sanitized verification evidence; never carries provider response text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strength: VerificationStrength
    verified_at: AwareDatetime
    safe_account_identity: (
        Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")] | None
    ) = None
    expires_at: AwareDatetime | None = None
    granted_permissions: frozenset[Permission] = frozenset()
    sanitized_failure: VerificationFailure | None = None

    @model_validator(mode="after")
    def _consistent(self) -> VerificationResult:
        if self.expires_at is not None and self.expires_at < self.verified_at:
            raise ValueError("expires_at cannot precede verification")
        if self.sanitized_failure is not None and self.safe_account_identity is not None:
            raise ValueError("failed verification cannot expose account identity")
        return self


class AccessStatus(StrictBaseModel):
    """Transport-safe current connection state and bounded evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    account_id: AccountId | None
    state: AccessStatusKind
    method_id: MethodId | None = None
    verification: VerificationResult | None = None

    @model_validator(mode="after")
    def _consistent(self) -> AccessStatus:
        connected = self.state is not AccessStatusKind.DISCONNECTED
        if connected != (self.method_id is not None):
            raise ValueError("method_id is required exactly when a connection exists")
        return self
