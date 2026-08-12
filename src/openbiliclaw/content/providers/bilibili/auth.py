"""Provider-owned Bilibili manual-cookie form and verifier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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

from .client import BilibiliClient, cookie_parts

BILIBILI_CONNECTION_FORM = ConnectionForm(
    provider_id="bilibili",
    method_id="builtin.manual",
    interaction=InteractionKind.SECRET_FORM,
    fields=(
        FormField(
            field_id="cookie",
            label="Bilibili Cookie",
            kind=FieldKind.COOKIE,
            secret=True,
            min_length=20,
            max_length=65_536,
            pattern=r"(?s)(?=.*(?:^|;\s*)SESSDATA=[^;]+)(?=.*(?:^|;\s*)bili_jct=[^;]+).+",
        ),
    ),
)


class _CookieSubmission(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cookie: str = Field(min_length=20, max_length=65_536)


class BilibiliCredentialVerifier:
    def __init__(self, client: BilibiliClient) -> None:
        self._client = client

    async def __call__(
        self, handle: CredentialAccessHandle, credential: memoryview, /
    ) -> VerificationResult:
        now = datetime.now(UTC)
        try:
            submission = _CookieSubmission.model_validate_json(credential.tobytes())
            session, csrf = cookie_parts(submission.cookie)
            if not session or not csrf:
                return self._failure(now, VerificationFailure.INVALID_CREDENTIAL)
            nav = await self._client.nav_with_cookie(submission.cookie)
        except ValidationError:
            return self._failure(now, VerificationFailure.INVALID_CREDENTIAL)
        except ContentIntegrationError as exc:
            if exc.code is IntegrationErrorCode.ACCESS_DENIED:
                return self._failure(now, VerificationFailure.EXPIRED)
            if exc.code is IntegrationErrorCode.RATE_LIMITED:
                return self._failure(now, VerificationFailure.RATE_LIMITED)
            return self._failure(now, VerificationFailure.NETWORK_UNAVAILABLE)
        if not nav.is_login:
            return self._failure(now, VerificationFailure.EXPIRED)
        return VerificationResult(
            strength=VerificationStrength.LIVE,
            verified_at=now,
            expires_at=now + timedelta(minutes=15),
            safe_account_identity=nav.name,
            granted_permissions=handle.permissions
            & frozenset({Permission.READ_PRIVATE, Permission.WRITE}),
        )

    @staticmethod
    def _failure(now: datetime, failure: VerificationFailure) -> VerificationResult:
        return VerificationResult(
            strength=VerificationStrength.NONE,
            verified_at=now,
            sanitized_failure=failure,
        )
