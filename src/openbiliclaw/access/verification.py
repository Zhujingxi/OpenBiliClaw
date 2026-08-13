"""Deterministic verification-cache and safe-status projection rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import (
    AccessHandle,
    AccessStatus,
    AccessStatusKind,
    Permission,
    VerificationFailure,
    VerificationResult,
)

if TYPE_CHECKING:
    from datetime import datetime, timedelta

_DEGRADED = {
    VerificationFailure.INSUFFICIENT_SCOPE,
    VerificationFailure.RATE_LIMITED,
    VerificationFailure.PROVIDER_RESPONSE_INVALID,
    VerificationFailure.SESSION_MODE_UNSUPPORTED,
}
_UNAVAILABLE = {
    VerificationFailure.GEO_BLOCKED,
    VerificationFailure.NETWORK_UNAVAILABLE,
}


def cache_is_valid(result: VerificationResult, *, now: datetime, maximum_age: timedelta) -> bool:
    """Successful evidence is cacheable only within both TTL and provider expiry."""

    if result.sanitized_failure is not None:
        return False
    deadline = result.verified_at + maximum_age
    if result.expires_at is not None:
        deadline = min(deadline, result.expires_at)
    return now < deadline


def enforce_requested_permissions(
    result: VerificationResult, requested: frozenset[Permission]
) -> VerificationResult:
    if result.sanitized_failure is None and not requested <= result.granted_permissions:
        return result.model_copy(
            update={
                "safe_account_identity": None,
                "sanitized_failure": VerificationFailure.INSUFFICIENT_SCOPE,
            }
        )
    return result


def project_status(
    handle: AccessHandle, method_id: str, result: VerificationResult, *, now: datetime
) -> AccessStatus:
    failure = result.sanitized_failure
    if failure is VerificationFailure.EXPIRED or (
        result.expires_at is not None and result.expires_at <= now
    ):
        state = AccessStatusKind.EXPIRED
    elif failure in _DEGRADED:
        state = AccessStatusKind.DEGRADED
    elif failure in _UNAVAILABLE:
        state = AccessStatusKind.UNAVAILABLE
    elif failure is VerificationFailure.INVALID_CREDENTIAL:
        state = AccessStatusKind.UNVERIFIED
    elif failure is None:
        state = AccessStatusKind.CONNECTED
    else:
        state = AccessStatusKind.UNVERIFIED
    return AccessStatus(
        provider_id=handle.provider_id,
        account_id=handle.account_id,
        state=state,
        method_id=method_id,
        verification=result,
    )
