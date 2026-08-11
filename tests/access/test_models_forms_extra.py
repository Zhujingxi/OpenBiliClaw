from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from openbiliclaw.access.forms import ConnectionForm, FieldKind, FormField
from openbiliclaw.access.models import (
    AccessRequest,
    AccessStatus,
    AccessStatusKind,
    AnonymousAccessHandle,
    InteractionKind,
    Permission,
    VerificationFailure,
    VerificationResult,
    VerificationStrength,
)
from openbiliclaw.access.verification import cache_is_valid, project_status


def _field(**updates: object) -> FormField:
    values: dict[str, object] = {
        "field_id": "value",
        "label": "Value",
        "kind": FieldKind.TEXT,
        "secret": True,
    }
    values.update(updates)
    return FormField.model_validate(values)


@pytest.mark.parametrize(
    "updates",
    [
        {"min_length": 4, "max_length": 2},
        {"pattern": "["},
        {"kind": FieldKind.TOKEN, "secret": False},
    ],
)
def test_form_field_rejects_invalid_descriptor(updates: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        _field(**updates)


@pytest.mark.parametrize(
    "fields,interaction",
    [
        ((_field(), _field()), InteractionKind.SECRET_FORM),
        ((_field(),), InteractionKind.NONE),
        ((_field(secret=False),), InteractionKind.SECRET_FORM),
    ],
)
def test_connection_form_rejects_unsafe_shape(
    fields: tuple[FormField, ...], interaction: InteractionKind
) -> None:
    with pytest.raises(ValidationError):
        ConnectionForm(
            provider_id="x",
            method_id="builtin.manual",
            interaction=interaction,
            fields=fields,
        )


def test_optional_field_omission_and_pattern_failure() -> None:
    form = ConnectionForm(
        provider_id="x",
        method_id="builtin.manual",
        interaction=InteractionKind.SECRET_FORM,
        fields=(
            _field(field_id="secret", pattern=r"^ok+$"),
            _field(field_id="optional", secret=False, required=False),
        ),
    )
    assert form.validate_submission({"secret": "okk"}).items() == (("secret", "okk"),)
    with pytest.raises(ValueError, match="shape"):
        form.validate_submission({"secret": "wrong"})


def test_request_and_handle_invariants() -> None:
    with pytest.raises(ValidationError):
        AccessRequest(provider_id="x", permissions=frozenset(), supported_method_ids=("a.b",))
    with pytest.raises(ValidationError):
        AccessRequest(
            provider_id="x",
            permissions=frozenset({Permission.READ_PUBLIC}),
            supported_method_ids=(),
        )
    with pytest.raises(ValidationError):
        AnonymousAccessHandle(
            provider_id="x",
            account_id="invented",
            permissions=frozenset({Permission.READ_PUBLIC}),
        )


def test_verification_and_status_invariants_and_expiry_projection() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        VerificationResult(
            strength=VerificationStrength.LIVE,
            verified_at=now,
            expires_at=now - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError):
        AccessStatus(
            provider_id="x",
            state=AccessStatusKind.CONNECTED,
            method_id=None,
        )
    handle = AnonymousAccessHandle(
        provider_id="x", account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )
    result = VerificationResult(
        strength=VerificationStrength.LIVE,
        verified_at=now,
        expires_at=now,
        granted_permissions=handle.permissions,
    )
    assert not cache_is_valid(result, now=now, maximum_age=timedelta(minutes=1))
    assert (
        project_status(handle, "builtin.anonymous", result, now=now).state
        is AccessStatusKind.EXPIRED
    )
    failed = result.model_copy(
        update={
            "expires_at": None,
            "sanitized_failure": VerificationFailure.SESSION_MODE_UNSUPPORTED,
        }
    )
    assert not cache_is_valid(failed, now=now, maximum_age=timedelta(minutes=1))
