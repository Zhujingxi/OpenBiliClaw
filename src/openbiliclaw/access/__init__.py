"""Typed, secret-safe Provider Access boundary."""

from .anonymous import AnonymousAccessMethod, AnonymousProbeOutcome, AnonymousProbeResult
from .broker import AccessBroker, AccessUnavailableError, OpenedAccess
from .forms import ConnectionForm, FieldKind, FormField, FormValidationError
from .manual import CredentialVerifier, ManualAccessMethod, ManualProviderSpec
from .methods import AccessMethod, AccessMethodRegistry
from .models import (
    AccessHandle,
    AccessMethodDescriptor,
    AccessRequest,
    AccessStatus,
    AccessStatusKind,
    AccountId,
    AnonymousAccessHandle,
    CredentialAccessHandle,
    CredentialRef,
    InteractionKind,
    MethodId,
    Permission,
    ProviderId,
    VerificationFailure,
    VerificationResult,
    VerificationStrength,
)
from .service import AccessService

__all__ = [
    "AccessBroker",
    "AccessHandle",
    "AccountId",
    "AccessMethod",
    "AccessMethodDescriptor",
    "AccessMethodRegistry",
    "AccessRequest",
    "AccessService",
    "AccessStatus",
    "AccessStatusKind",
    "AccessUnavailableError",
    "AnonymousAccessHandle",
    "AnonymousAccessMethod",
    "AnonymousProbeOutcome",
    "AnonymousProbeResult",
    "ConnectionForm",
    "CredentialAccessHandle",
    "CredentialRef",
    "CredentialVerifier",
    "FieldKind",
    "FormField",
    "FormValidationError",
    "InteractionKind",
    "ManualAccessMethod",
    "ManualProviderSpec",
    "MethodId",
    "OpenedAccess",
    "Permission",
    "ProviderId",
    "VerificationFailure",
    "VerificationResult",
    "VerificationStrength",
]
