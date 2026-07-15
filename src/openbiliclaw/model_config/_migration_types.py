"""Immutable public and private values for legacy model migration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from .types import (
        ChatConnection,
        EmbeddingModelSettings,
        EmbeddingProviderConfig,
        IssueSeverity,
        ModelConfig,
    )

MigrationAction: TypeAlias = Literal[
    "add_to_chat_route",
    "confirm_remove_after_backup",
    "cancel",
    "accept_global_route",
    "apply_shared_embedding_settings",
    "remove_embedding_fallback",
]

CONFIRM_REMOVE_ACTIONS: tuple[MigrationAction, ...] = (
    "confirm_remove_after_backup",
    "cancel",
)
UNROUTED_ACTIONS: tuple[MigrationAction, ...] = (
    "add_to_chat_route",
    "confirm_remove_after_backup",
    "cancel",
)
MODULE_OVERRIDE_ACTIONS: tuple[MigrationAction, ...] = (
    "accept_global_route",
    "cancel",
)
EMBEDDING_MISMATCH_ACTIONS: tuple[MigrationAction, ...] = (
    "apply_shared_embedding_settings",
    "remove_embedding_fallback",
    "cancel",
)


@dataclass(frozen=True)
class MigrationIssue:
    """One public, secret-free legacy migration decision or notice."""

    id: str
    code: str
    field: str
    provider: str = ""
    credential_configured: bool = False
    reason: str = ""
    severity: IssueSeverity = "blocking"
    allowed_actions: tuple[MigrationAction, ...] = ()

    def __post_init__(self) -> None:
        """Freeze action order supplied by permissive callers."""
        object.__setattr__(self, "allowed_actions", tuple(self.allowed_actions))


@dataclass(frozen=True)
class MigrationReport:
    """Deterministic public report produced from one legacy table."""

    issues: tuple[MigrationIssue, ...] = ()

    def __post_init__(self) -> None:
        """Keep issue order immutable for stable revisions and API output."""
        object.__setattr__(self, "issues", tuple(self.issues))

    @property
    def issue_codes(self) -> set[str]:
        """Return a fresh set of issue codes for callers and tests."""
        return {issue.code for issue in self.issues}

    @property
    def has_pending_decisions(self) -> bool:
        """Whether explicit blocking choices are still required."""
        return any(issue.severity == "blocking" for issue in self.issues)


@dataclass(frozen=True)
class MigrationResolution:
    """JSON-translatable choice for one migration issue."""

    action: MigrationAction
    position: int | None = None
    embedding_settings: EmbeddingModelSettings | None = None


@dataclass(frozen=True)
class _EmbeddingSpace:
    """Private effective vector-space facts for one fallback candidate."""

    provider: str
    model: str
    fixed_output_dimensionality: int | None = None
    output_dimensionality_configurable: bool = False
    multimodal_capable: bool = False


@dataclass(frozen=True)
class _EmbeddingProviderState:
    """Private, secret-free compatibility facts for one mapped provider."""

    provider_id: str
    space: _EmbeddingSpace
    endpoint_valid: bool = True


@dataclass(frozen=True)
class _PendingValue:
    """Private payload attached to one blocking migration issue."""

    issue_id: str
    chat_connection: ChatConnection | None = field(default=None, repr=False)
    embedding_provider: EmbeddingProviderConfig | None = field(default=None, repr=False)
    embedding_state: _EmbeddingProviderState | None = field(default=None, repr=False)
    remove_chat_connection_id: str = field(default="", repr=False)
    remove_embedding_provider_id: str = field(default="", repr=False)


@dataclass(frozen=True)
class LegacyMigrationResult:
    """In-memory candidate, safe report, and private resolution payloads."""

    models: ModelConfig
    report: MigrationReport
    _pending: tuple[_PendingValue, ...] = field(default=(), repr=False, compare=False)
    _embedding_states: tuple[_EmbeddingProviderState, ...] = field(
        default=(), repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Prevent callers from mutating private migration metadata."""
        object.__setattr__(self, "_pending", tuple(self._pending))
        object.__setattr__(self, "_embedding_states", tuple(self._embedding_states))


class MigrationResolutionError(ValueError):
    """Raised when migration choices are missing, unknown, or malformed."""


__all__ = [
    "EMBEDDING_MISMATCH_ACTIONS",
    "CONFIRM_REMOVE_ACTIONS",
    "LegacyMigrationResult",
    "MODULE_OVERRIDE_ACTIONS",
    "MigrationAction",
    "MigrationIssue",
    "MigrationReport",
    "MigrationResolution",
    "MigrationResolutionError",
    "UNROUTED_ACTIONS",
    "_EmbeddingProviderState",
    "_EmbeddingSpace",
    "_PendingValue",
]
