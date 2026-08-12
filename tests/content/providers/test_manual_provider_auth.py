from __future__ import annotations

import json
from typing import Protocol

import pytest

from openbiliclaw.access.forms import ConnectionForm
from openbiliclaw.access.models import (
    CredentialAccessHandle,
    Permission,
    VerificationFailure,
    VerificationResult,
)
from openbiliclaw.content.providers.linuxdo.auth import (
    LINUXDO_CONNECTION_FORM,
    LinuxDoCredentialVerifier,
)
from openbiliclaw.content.providers.reddit.auth import (
    REDDIT_CONNECTION_FORM,
    RedditCredentialVerifier,
)
from openbiliclaw.content.providers.x.auth import X_CONNECTION_FORM, XCredentialVerifier
from openbiliclaw.content.providers.zhihu.auth import ZHIHU_CONNECTION_FORM, ZhihuCredentialVerifier


class Probe:
    def __init__(self, result: str | None = "alice", error: str | None = None) -> None:
        self.result, self.error, self.seen = result, error, ""

    async def __call__(self, credential: str) -> str | None:
        self.seen = credential
        if self.error:
            raise RuntimeError(self.error)
        return self.result


class Verifier(Protocol):
    async def __call__(
        self, handle: CredentialAccessHandle, credential: memoryview, /
    ) -> VerificationResult: ...


class VerifierFactory(Protocol):
    def __call__(self, probe: Probe) -> Verifier: ...


Case = tuple[str, ConnectionForm, VerifierFactory, dict[str, str]]
CASES: tuple[Case, ...] = (
    (
        "reddit",
        REDDIT_CONNECTION_FORM,
        RedditCredentialVerifier,
        {"cookie": "reddit_session=" + "a" * 20},
    ),
    (
        "x",
        X_CONNECTION_FORM,
        XCredentialVerifier,
        {"cookie": "auth_token=" + "a" * 20 + "; ct0=" + "b" * 20},
    ),
    ("zhihu", ZHIHU_CONNECTION_FORM, ZhihuCredentialVerifier, {"cookie": "z_c0=" + "a" * 20}),
    ("linuxdo", LINUXDO_CONNECTION_FORM, LinuxDoCredentialVerifier, {"cookie": "_t=" + "a" * 20}),
)


@pytest.mark.parametrize("provider,form,verifier_type,submission", CASES)
async def test_exact_forms_and_safe_verification(
    provider: str, form: ConnectionForm, verifier_type: VerifierFactory, submission: dict[str, str]
) -> None:
    assert form.provider_id == provider and len(form.fields) == 1 and form.fields[0].secret
    probe = Probe()
    verifier = verifier_type(probe)
    handle = CredentialAccessHandle(
        provider_id=provider,
        account_id="acct",
        permissions=frozenset({Permission.READ_PRIVATE}),
        credential_ref="cred_" + "1" * 32,
        revision=1,
    )
    result = await verifier(handle, memoryview(json.dumps(submission).encode()))
    assert result.safe_account_identity == "alice"
    assert "CANARY" not in result.model_dump_json()
    malformed = await verifier(handle, memoryview(b"{}"))
    assert malformed.sanitized_failure is VerificationFailure.INVALID_CREDENTIAL


@pytest.mark.parametrize("provider,form,verifier_type,submission", CASES)
async def test_probe_failures_are_sanitized(
    provider: str, form: ConnectionForm, verifier_type: VerifierFactory, submission: dict[str, str]
) -> None:
    verifier = verifier_type(Probe(error="CANARY response body"))
    handle = CredentialAccessHandle(
        provider_id=provider,
        account_id="acct",
        permissions=frozenset({Permission.READ_PRIVATE}),
        credential_ref="cred_" + "1" * 32,
        revision=1,
    )
    result = await verifier(handle, memoryview(json.dumps(submission).encode()))
    assert result.sanitized_failure is VerificationFailure.NETWORK_UNAVAILABLE
    assert "CANARY" not in result.model_dump_json()


@pytest.mark.parametrize("provider,form,verifier_type,submission", CASES)
async def test_expired_and_missing_cookie_marker(
    provider: str, form: ConnectionForm, verifier_type: VerifierFactory, submission: dict[str, str]
) -> None:
    handle = CredentialAccessHandle(
        provider_id=provider,
        account_id="acct",
        permissions=frozenset({Permission.READ_PRIVATE}),
        credential_ref="cred_" + "1" * 32,
        revision=1,
    )
    expired = await verifier_type(Probe(result=None))(
        handle, memoryview(json.dumps(submission).encode())
    )
    assert expired.sanitized_failure is VerificationFailure.EXPIRED
    wrong = await verifier_type(Probe())(
        handle, memoryview(json.dumps({"cookie": "wrong=" + "x" * 20}).encode())
    )
    assert wrong.sanitized_failure is VerificationFailure.INVALID_CREDENTIAL
