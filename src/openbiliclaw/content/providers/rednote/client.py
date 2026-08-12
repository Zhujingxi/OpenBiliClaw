"""Typed RedNote payload boundary; no browser/session task transport."""

from pydantic import ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode

from .models import RednoteResponse


class RednoteClient:
    """Validate trusted-ingress envelopes without executing a session-bound read."""

    def parse(self, raw: bytes) -> RednoteResponse:
        try:
            response = RednoteResponse.model_validate_json(raw)
        except ValidationError as exc:
            raise ContentIntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider returned invalid data"
            ) from exc
        if response.code == 0:
            return response
        if response.code == -101:
            raise ContentIntegrationError(IntegrationErrorCode.ACCESS_DENIED, "access unavailable")
        if response.code in {412, 429}:
            raise ContentIntegrationError(
                IntegrationErrorCode.RATE_LIMITED, "provider rate limited"
            )
        raise ContentIntegrationError(
            IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider request failed"
        )
