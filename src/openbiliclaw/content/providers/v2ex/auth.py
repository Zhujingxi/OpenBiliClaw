"""Provider-owned V2EX PAT form and verifier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import ConfigDict, Field, ValidationError

from openbiliclaw.access.forms import ConnectionForm, FieldKind, FormField
from openbiliclaw.access.models import (
    CredentialAccessHandle,
    InteractionKind,
    Permission,
    VerificationFailure,
    VerificationResult,
    VerificationStrength,
)
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.core._pydantic import StrictBaseModel

V2EX_CONNECTION_FORM = ConnectionForm(
    provider_id="v2ex",
    method_id="builtin.manual",
    interaction=InteractionKind.SECRET_FORM,
    fields=(
        FormField(
            field_id="token",
            label="V2EX Personal Access Token",
            kind=FieldKind.TOKEN,
            secret=True,
            min_length=8,
            max_length=512,
            pattern=r"^[!-~]+$",
        ),
    ),
)


class IdentityClient(Protocol):
    async def identity(self, token: str) -> str: ...


class _Submission(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token: str = Field(min_length=8, max_length=512, pattern=r"^[!-~]+$")


class V2EXCredentialVerifier:
    def __init__(self, client: IdentityClient) -> None:
        self._client = client

    async def __call__(
        self, handle: CredentialAccessHandle, credential: memoryview, /
    ) -> VerificationResult:
        now = datetime.now(UTC)
        try:
            token = _Submission.model_validate_json(credential.tobytes()).token
            identity = await self._client.identity(token)
        except ValidationError:
            return self._failure(now, VerificationFailure.INVALID_CREDENTIAL)
        except ContentIntegrationError as exc:
            failure = {
                IntegrationErrorCode.ACCESS_DENIED: VerificationFailure.EXPIRED,
                IntegrationErrorCode.RATE_LIMITED: VerificationFailure.RATE_LIMITED,
            }.get(exc.code, VerificationFailure.NETWORK_UNAVAILABLE)
            return self._failure(now, failure)
        return VerificationResult(
            strength=VerificationStrength.LIVE,
            verified_at=now,
            expires_at=now + timedelta(minutes=15),
            safe_account_identity=identity,
            granted_permissions=handle.permissions
            & frozenset({Permission.READ_PUBLIC, Permission.READ_PRIVATE}),
        )

    @staticmethod
    def _failure(now: datetime, failure: VerificationFailure) -> VerificationResult:
        return VerificationResult(
            strength=VerificationStrength.NONE, verified_at=now, sanitized_failure=failure
        )
