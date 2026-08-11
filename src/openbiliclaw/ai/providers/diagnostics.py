"""Safe provider construction diagnostics independent of agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DiagnosticStatus(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


class DiagnosticDetail(StrEnum):
    CONSTRUCTION_FAILED = "provider construction failed"
    CAPABILITY_UNSUPPORTED = "required capability unsupported"


@dataclass(frozen=True, slots=True)
class ProviderDiagnostic:
    provider: str
    instance_id: str
    status: DiagnosticStatus
    detail: DiagnosticDetail | None = None

    def __post_init__(self) -> None:
        if not self.provider or not self.instance_id:
            raise ValueError("provider and instance ID must not be empty")


def construction_diagnostic(
    provider: str, instance_id: str, error: BaseException | None = None
) -> ProviderDiagnostic:
    """Report construction health without copying unsafe exception text."""

    if error is None:
        return ProviderDiagnostic(provider, instance_id, DiagnosticStatus.READY)
    return ProviderDiagnostic(
        provider,
        instance_id,
        DiagnosticStatus.UNAVAILABLE,
        DiagnosticDetail.CONSTRUCTION_FAILED,
    )
