"""Immutable domain values for model connections and ordered routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

CredentialSource: TypeAlias = Literal["none", "inline", "env", "oauth"]
IssueSeverity: TypeAlias = Literal["warning", "blocking"]


@dataclass(frozen=True)
class CredentialConfig:
    """A credential source and its private value or reference.

    ``value`` holds an inline secret, an environment variable name, or an OAuth
    credential reference according to ``source``. It is deliberately excluded
    from repr output so nested connection representations remain secret-safe.
    """

    source: CredentialSource = "none"
    value: str = field(default="", repr=False)


@dataclass(frozen=True)
class ChatConnection:
    """One chat-capable connection in an ordered route."""

    id: str
    name: str
    type: str
    model: str
    preset: str = ""
    base_url: str = ""
    credential: CredentialConfig = field(default_factory=CredentialConfig)
    api_mode: str = ""
    reasoning_effort: str = ""
    http_referer: str = ""
    x_title: str = ""
    num_ctx: int = 0


@dataclass(frozen=True)
class ChatRouteConfig:
    """Ordered chat connections and route-wide execution settings."""

    connections: tuple[ChatConnection, ...] = ()
    concurrency: int = 4
    timeout_seconds: int = 300

    def __post_init__(self) -> None:
        """Keep the ordered collection immutable even for permissive callers."""
        object.__setattr__(self, "connections", tuple(self.connections))

    def role_at(self, index: int) -> str:
        """Return the role derived exclusively from a connection's position."""
        if index < 0 or index >= len(self.connections):
            raise IndexError("chat connection index out of range")
        return "primary" if index == 0 else f"fallback_{index}"


@dataclass(frozen=True)
class EmbeddingModelSettings:
    """Model-space settings shared by every provider in an embedding route."""

    model: str
    output_dimensionality: int = 1024
    similarity_threshold: float = 0.82
    multimodal_enabled: bool = False


@dataclass(frozen=True)
class EmbeddingProviderConfig:
    """One provider for the route-wide embedding model settings."""

    id: str
    name: str
    type: str
    preset: str = ""
    base_url: str = ""
    credential: CredentialConfig = field(default_factory=CredentialConfig)


@dataclass(frozen=True)
class EmbeddingRouteConfig:
    """An ordered embedding provider route with one shared model space."""

    enabled: bool = False
    settings: EmbeddingModelSettings = field(
        default_factory=lambda: EmbeddingModelSettings(model="")
    )
    providers: tuple[EmbeddingProviderConfig, ...] = ()

    def __post_init__(self) -> None:
        """Keep the ordered collection immutable even for permissive callers."""
        object.__setattr__(self, "providers", tuple(self.providers))


@dataclass(frozen=True)
class ModelConfig:
    """Versioned model configuration independent of persistence concerns."""

    schema_version: int = 1
    chat: ChatRouteConfig = field(default_factory=ChatRouteConfig)
    embedding: EmbeddingRouteConfig = field(default_factory=EmbeddingRouteConfig)


@dataclass(frozen=True)
class ModelConfigIssue:
    """A field-addressable model configuration validation issue."""

    path: str
    code: str
    message: str
    severity: IssueSeverity = "blocking"
    connection_id: str | None = None
