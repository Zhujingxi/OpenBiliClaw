from __future__ import annotations

import json

from openbiliclaw.access.models import (
    CredentialAccessHandle,
    Permission,
    VerificationFailure,
    VerificationStrength,
)
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.providers.bangumi.auth import (
    BANGUMI_CONNECTION_FORM,
    BangumiCredentialVerifier,
)
from openbiliclaw.content.providers.v2ex.auth import V2EX_CONNECTION_FORM, V2EXCredentialVerifier


class IdentityClient:
    def __init__(
        self, identity: str | None = "alice", error: IntegrationErrorCode | None = None
    ) -> None:
        self._identity = identity
        self.error = error
        self.tokens: list[str] = []

    async def identity(self, token: str) -> str:
        self.tokens.append(token)
        if self.error is not None:
            raise ContentIntegrationError(self.error, "safe failure")
        if self._identity is None:
            raise ContentIntegrationError(IntegrationErrorCode.ACCESS_DENIED, "invalid token")
        return self._identity


def handle(provider: str) -> CredentialAccessHandle:
    return CredentialAccessHandle(
        provider_id=provider,
        account_id="alice",
        permissions=frozenset({Permission.READ_PUBLIC, Permission.READ_PRIVATE}),
        credential_ref="cred_" + "a" * 32,
        revision=1,
    )


async def test_bangumi_token_form_and_verification() -> None:
    client = IdentityClient()
    verifier = BangumiCredentialVerifier(client)
    result = await verifier(
        handle("bangumi"), memoryview(json.dumps({"token": "token-value"}).encode())
    )
    assert BANGUMI_CONNECTION_FORM.provider_id == "bangumi"
    assert result.strength is VerificationStrength.LIVE
    assert result.safe_account_identity == "alice"
    assert client.tokens == ["token-value"]
    assert "token-value" not in result.model_dump_json()


async def test_v2ex_token_form_and_verification() -> None:
    client = IdentityClient()
    verifier = V2EXCredentialVerifier(client)
    result = await verifier(
        handle("v2ex"), memoryview(json.dumps({"token": "token-value"}).encode())
    )
    assert V2EX_CONNECTION_FORM.provider_id == "v2ex"
    assert result.strength is VerificationStrength.LIVE
    assert result.granted_permissions == frozenset(
        {Permission.READ_PUBLIC, Permission.READ_PRIVATE}
    )


async def test_invalid_and_rate_limited_credentials_are_sanitized() -> None:
    invalid = await BangumiCredentialVerifier(IdentityClient())(
        handle("bangumi"), memoryview(b"not-json")
    )
    assert invalid.sanitized_failure is VerificationFailure.INVALID_CREDENTIAL
    limited = await V2EXCredentialVerifier(IdentityClient(error=IntegrationErrorCode.RATE_LIMITED))(
        handle("v2ex"), memoryview(json.dumps({"token": "SECRET-CANARY"}).encode())
    )
    assert limited.sanitized_failure is VerificationFailure.RATE_LIMITED
    assert "SECRET-CANARY" not in limited.model_dump_json()
