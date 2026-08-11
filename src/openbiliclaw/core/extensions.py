"""Closed set of typed runtime extension registrations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from openbiliclaw.core.jobs import JobSpec

_EXTENSION_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")


class ExtensionKind(StrEnum):
    MODEL_PROVIDER = "model_provider"
    EMBEDDING_PROVIDER = "embedding_provider"
    CONTENT_PROVIDER = "content_provider"
    ACCESS_METHOD = "access_method"
    OBSERVATION_PROVIDER = "observation_provider"
    UNDERSTANDING_ANALYZER = "understanding_analyzer"
    DISCOVERY_STRATEGY = "discovery_strategy"
    ASSISTANT_SKILL = "assistant_skill"
    PRESENTATION = "presentation"


def _validate(extension_id: str, capability_version: int) -> None:
    if _EXTENSION_ID.fullmatch(extension_id) is None:
        raise ValueError("extension_id must be a namespaced lowercase identifier")
    if capability_version < 1:
        raise ValueError("capability_version must be positive")


@dataclass(frozen=True, slots=True)
class ModelProviderRegistration:
    extension_id: str
    capability_version: int
    jobs: tuple[JobSpec, ...] = ()
    kind: Literal[ExtensionKind.MODEL_PROVIDER] = field(
        default=ExtensionKind.MODEL_PROVIDER, init=False
    )

    def __post_init__(self) -> None:
        _validate(self.extension_id, self.capability_version)


@dataclass(frozen=True, slots=True)
class EmbeddingProviderRegistration:
    extension_id: str
    capability_version: int
    jobs: tuple[JobSpec, ...] = ()
    kind: Literal[ExtensionKind.EMBEDDING_PROVIDER] = field(
        default=ExtensionKind.EMBEDDING_PROVIDER, init=False
    )

    def __post_init__(self) -> None:
        _validate(self.extension_id, self.capability_version)


@dataclass(frozen=True, slots=True)
class ContentProviderRegistration:
    extension_id: str
    capability_version: int
    jobs: tuple[JobSpec, ...] = ()
    kind: Literal[ExtensionKind.CONTENT_PROVIDER] = field(
        default=ExtensionKind.CONTENT_PROVIDER, init=False
    )

    def __post_init__(self) -> None:
        _validate(self.extension_id, self.capability_version)


@dataclass(frozen=True, slots=True)
class AccessMethodRegistration:
    extension_id: str
    capability_version: int
    jobs: tuple[JobSpec, ...] = ()
    kind: Literal[ExtensionKind.ACCESS_METHOD] = field(
        default=ExtensionKind.ACCESS_METHOD, init=False
    )

    def __post_init__(self) -> None:
        _validate(self.extension_id, self.capability_version)


@dataclass(frozen=True, slots=True)
class ObservationProviderRegistration:
    extension_id: str
    capability_version: int
    jobs: tuple[JobSpec, ...] = ()
    kind: Literal[ExtensionKind.OBSERVATION_PROVIDER] = field(
        default=ExtensionKind.OBSERVATION_PROVIDER, init=False
    )

    def __post_init__(self) -> None:
        _validate(self.extension_id, self.capability_version)


@dataclass(frozen=True, slots=True)
class UnderstandingAnalyzerRegistration:
    extension_id: str
    capability_version: int
    jobs: tuple[JobSpec, ...] = ()
    kind: Literal[ExtensionKind.UNDERSTANDING_ANALYZER] = field(
        default=ExtensionKind.UNDERSTANDING_ANALYZER, init=False
    )

    def __post_init__(self) -> None:
        _validate(self.extension_id, self.capability_version)


@dataclass(frozen=True, slots=True)
class DiscoveryStrategyRegistration:
    extension_id: str
    capability_version: int
    jobs: tuple[JobSpec, ...] = ()
    kind: Literal[ExtensionKind.DISCOVERY_STRATEGY] = field(
        default=ExtensionKind.DISCOVERY_STRATEGY, init=False
    )

    def __post_init__(self) -> None:
        _validate(self.extension_id, self.capability_version)


@dataclass(frozen=True, slots=True)
class AssistantSkillRegistration:
    extension_id: str
    capability_version: int
    jobs: tuple[JobSpec, ...] = ()
    kind: Literal[ExtensionKind.ASSISTANT_SKILL] = field(
        default=ExtensionKind.ASSISTANT_SKILL, init=False
    )

    def __post_init__(self) -> None:
        _validate(self.extension_id, self.capability_version)


@dataclass(frozen=True, slots=True)
class PresentationRegistration:
    extension_id: str
    capability_version: int
    jobs: tuple[JobSpec, ...] = ()
    kind: Literal[ExtensionKind.PRESENTATION] = field(
        default=ExtensionKind.PRESENTATION, init=False
    )

    def __post_init__(self) -> None:
        _validate(self.extension_id, self.capability_version)


ExtensionRegistration: TypeAlias = (
    ModelProviderRegistration
    | EmbeddingProviderRegistration
    | ContentProviderRegistration
    | AccessMethodRegistration
    | ObservationProviderRegistration
    | UnderstandingAnalyzerRegistration
    | DiscoveryStrategyRegistration
    | AssistantSkillRegistration
    | PresentationRegistration
)


class ExtensionRegistry:
    """Validate a closed registration union; it is not a service locator."""

    def __init__(self, *, supported_capability_version: int) -> None:
        if supported_capability_version < 1:
            raise ValueError("supported_capability_version must be positive")
        self._supported_capability_version = supported_capability_version
        self._registrations: list[ExtensionRegistration] = []
        self._ids: set[str] = set()

    @property
    def registrations(self) -> tuple[ExtensionRegistration, ...]:
        return tuple(self._registrations)

    def by_kind(self, kind: ExtensionKind) -> tuple[ExtensionRegistration, ...]:
        return tuple(item for item in self._registrations if item.kind is kind)

    def register(self, registration: ExtensionRegistration) -> None:
        if registration.extension_id in self._ids:
            raise ValueError(f"duplicate extension_id: {registration.extension_id}")
        if registration.capability_version != self._supported_capability_version:
            raise ValueError(
                f"incompatible extension capability version {registration.capability_version}; "
                f"expected {self._supported_capability_version}"
            )
        self._ids.add(registration.extension_id)
        self._registrations.append(registration)
