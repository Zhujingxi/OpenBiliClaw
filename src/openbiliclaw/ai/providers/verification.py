"""Declared-versus-verified provider capability records and opt-in probes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from openbiliclaw.ai.providers.models.config import ModelInstanceConfig


class CapabilityStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    capability: str
    status: CapabilityStatus


@dataclass(frozen=True, slots=True)
class VerifiedCapabilities:
    """Probe results associated with a complete non-secret config key."""

    key: str
    results: tuple[CapabilityResult, ...]

    @classmethod
    def unverified(cls, config: ModelInstanceConfig) -> VerifiedCapabilities:
        return cls(
            key=verification_key(config),
            results=tuple(
                CapabilityResult(name, CapabilityStatus.UNVERIFIED)
                for name in ("structured_output", "tools", "vision", "streaming")
            ),
        )


class CapabilityVerificationStore:
    """Non-secret probe records keyed by the complete model identity."""

    def __init__(self) -> None:
        self._records: dict[str, VerifiedCapabilities] = {}

    def put(self, record: VerifiedCapabilities) -> None:
        self._records[record.key] = record

    def get(self, config: ModelInstanceConfig) -> VerifiedCapabilities | None:
        return self._records.get(verification_key(config))


class UnsupportedCapabilityError(RuntimeError):
    """A provider cannot honor a native capability contract."""


class CapabilityProbe:
    """Small opt-in probes; callers supply the real integration operation."""

    @staticmethod
    async def run(
        config: ModelInstanceConfig,
        capability: str,
        operation: Callable[[], Awaitable[bool]],
    ) -> CapabilityResult:
        if capability not in {"structured_output", "tools", "vision", "streaming"}:
            raise ValueError("unknown capability probe")
        try:
            supported = await operation()
        except Exception:
            return CapabilityResult(capability, CapabilityStatus.FAILED)
        return CapabilityResult(
            capability,
            CapabilityStatus.VERIFIED if supported else CapabilityStatus.UNSUPPORTED,
        )

    @classmethod
    async def structured_output(
        cls, config: ModelInstanceConfig, operation: Callable[[], Awaitable[bool]]
    ) -> CapabilityResult:
        return await cls.run(config, "structured_output", operation)

    @classmethod
    async def native_tools(
        cls, config: ModelInstanceConfig, operation: Callable[[], Awaitable[bool]]
    ) -> CapabilityResult:
        return await cls.run(config, "tools", operation)

    @classmethod
    async def vision(
        cls, config: ModelInstanceConfig, operation: Callable[[], Awaitable[bool]]
    ) -> CapabilityResult:
        return await cls.run(config, "vision", operation)

    @classmethod
    async def streaming(
        cls, config: ModelInstanceConfig, operation: Callable[[], Awaitable[bool]]
    ) -> CapabilityResult:
        return await cls.run(config, "streaming", operation)

    @staticmethod
    def local_support(config: ModelInstanceConfig, capability: str) -> CapabilityResult:
        """Fail local capabilities closed until an actual probe verifies them."""

        if capability not in {"structured_output", "tools", "vision", "streaming"}:
            raise ValueError("unknown capability probe")
        return CapabilityResult(capability, CapabilityStatus.UNSUPPORTED)


def verification_key(config: ModelInstanceConfig) -> str:
    return config.fingerprint()
