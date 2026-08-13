"""Closed, transport-safe Content Integration failures."""

from __future__ import annotations

from enum import StrEnum


class IntegrationErrorCode(StrEnum):
    UNAVAILABLE_CAPABILITY = "unavailable_capability"
    INVALID_CONTENT_REF = "invalid_content_ref"
    ACCESS_DENIED = "access_denied"
    RATE_LIMITED = "rate_limited"
    NETWORK_UNAVAILABLE = "network_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class ContentIntegrationError(Exception):
    """Normalized failure without provider response bodies or credentials."""

    def __init__(self, code: IntegrationErrorCode, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(f"{code.value}: {safe_message}")
