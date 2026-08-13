"""Model construction, capability verification, and embedding plugins."""

from .diagnostics import (
    DiagnosticDetail,
    DiagnosticStatus,
    ProviderDiagnostic,
    construction_diagnostic,
)
from .models import BuiltModel, ModelFactory, ModelInstanceConfig, ModelOptions
from .verification import (
    CapabilityProbe,
    CapabilityResult,
    CapabilityStatus,
    CapabilityVerificationStore,
    UnsupportedCapabilityError,
    VerifiedCapabilities,
    verification_key,
)

__all__ = [
    "BuiltModel",
    "CapabilityProbe",
    "CapabilityResult",
    "CapabilityStatus",
    "CapabilityVerificationStore",
    "DiagnosticDetail",
    "DiagnosticStatus",
    "ModelFactory",
    "ModelInstanceConfig",
    "ModelOptions",
    "ProviderDiagnostic",
    "UnsupportedCapabilityError",
    "VerifiedCapabilities",
    "construction_diagnostic",
    "verification_key",
]
