from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from openbiliclaw.access.forms import (
    ConnectionForm,
    FieldKind,
    FormField,
    FormValidationError,
)
from openbiliclaw.access.models import (
    AccessHandle,
    AccessStatus,
    AccessStatusKind,
    AnonymousAccessHandle,
    CredentialAccessHandle,
    InteractionKind,
    Permission,
    VerificationFailure,
    VerificationResult,
    VerificationStrength,
)


def test_handles_are_frozen_scoped_discriminated_and_secret_free() -> None:
    anonymous = AnonymousAccessHandle(
        provider_id="bilibili", account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )
    credential = CredentialAccessHandle(
        provider_id="github",
        account_id="account-1",
        permissions=frozenset({Permission.READ_PRIVATE}),
        credential_ref="cred_" + "a" * 32,
        revision=1,
    )
    adapter: TypeAdapter[AccessHandle] = TypeAdapter(AccessHandle)
    assert adapter.validate_json(anonymous.model_dump_json()).kind == "anonymous"
    serialized = credential.model_dump_json()
    assert "secret" not in serialized.lower()
    assert "token" not in serialized.lower()


def test_status_and_verification_are_safe_closed_models() -> None:
    now = datetime.now(UTC)
    result = VerificationResult(
        strength=VerificationStrength.LIVE,
        verified_at=now,
        granted_permissions=frozenset({Permission.READ_PRIVATE}),
        safe_account_identity="public-name",
    )
    status = AccessStatus(
        provider_id="github",
        account_id="account-1",
        state=AccessStatusKind.CONNECTED,
        method_id="builtin.manual",
        verification=result,
    )
    assert status.model_dump_json()
    with pytest.raises(ValidationError):
        VerificationResult(
            strength=VerificationStrength.NONE,
            verified_at=now,
            sanitized_failure=VerificationFailure.INVALID_CREDENTIAL,
            safe_account_identity="has\nnewline",
        )


def test_connection_form_validates_without_echoing_values() -> None:
    form = ConnectionForm(
        provider_id="github",
        method_id="builtin.manual",
        interaction=InteractionKind.SECRET_FORM,
        fields=(
            FormField(
                field_id="pat",
                label="Personal access token",
                kind=FieldKind.TOKEN,
                secret=True,
                min_length=8,
                pattern=r"^[A-Za-z0-9_-]+$",
            ),
            FormField(
                field_id="region",
                label="Region",
                kind=FieldKind.TEXT,
                secret=False,
                required=False,
                max_length=8,
            ),
        ),
    )
    canary = "".join(chr(code) for code in (99, 97, 110, 97, 114, 121, 49, 50))
    validated = form.validate_submission({"pat": canary, "region": "eu"})
    assert validated.value("pat") == canary
    assert canary not in repr(validated)
    assert "pat=<redacted>" in repr(validated)

    for bad in ({}, {"pat": "short"}, {"pat": canary, "unknown": "x"}):
        with pytest.raises(FormValidationError) as exc:
            form.validate_submission(bad)
        assert canary not in str(exc.value)
