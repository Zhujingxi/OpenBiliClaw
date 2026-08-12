"""Provider-owned Zhihu manual-cookie form and verifier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import ConfigDict, Field, ValidationError

from openbiliclaw.access.forms import ConnectionForm, FieldKind, FormField
from openbiliclaw.access.models import (
    CredentialAccessHandle,
    InteractionKind,
    VerificationFailure,
    VerificationResult,
    VerificationStrength,
)
from openbiliclaw.core._pydantic import StrictBaseModel

ZHIHU_CONNECTION_FORM = ConnectionForm(
    provider_id="zhihu",
    method_id="builtin.manual",
    interaction=InteractionKind.SECRET_FORM,
    fields=(
        FormField(
            field_id="cookie",
            label="Zhihu Cookie",
            kind=FieldKind.COOKIE,
            secret=True,
            min_length=20,
            max_length=65_536,
            pattern=r"(?s)(?=.*(?:^|;\s*)z_c0=[^;]+).+",
        ),
    ),
)


class CredentialProbe(Protocol):
    async def __call__(self, credential: str) -> str | None: ...


class _Submission(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cookie: str = Field(min_length=20, max_length=65_536)


class ZhihuCredentialVerifier:
    def __init__(self, probe: CredentialProbe) -> None:
        self._probe = probe

    async def __call__(
        self, handle: CredentialAccessHandle, credential: memoryview, /
    ) -> VerificationResult:
        now = datetime.now(UTC)
        try:
            submission = _Submission.model_validate_json(credential.tobytes())
            if "z_c0=" not in submission.cookie:
                return self._failure(now, VerificationFailure.INVALID_CREDENTIAL)
            identity = await self._probe(submission.cookie)
        except ValidationError:
            return self._failure(now, VerificationFailure.INVALID_CREDENTIAL)
        except Exception:
            return self._failure(now, VerificationFailure.NETWORK_UNAVAILABLE)
        if not identity:
            return self._failure(now, VerificationFailure.EXPIRED)
        return VerificationResult(
            strength=VerificationStrength.LIVE,
            verified_at=now,
            expires_at=now + timedelta(minutes=15),
            safe_account_identity=identity,
            granted_permissions=handle.permissions,
        )

    @staticmethod
    def _failure(now: datetime, failure: VerificationFailure) -> VerificationResult:
        return VerificationResult(
            strength=VerificationStrength.NONE, verified_at=now, sanitized_failure=failure
        )
