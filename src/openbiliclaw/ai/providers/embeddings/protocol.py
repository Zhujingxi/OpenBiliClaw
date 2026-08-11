"""Typed embedding contracts and provenance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

Vector = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingModelInfo:
    provider: str
    model: str
    dimensions: int
    normalized: bool
    version: str

    def __post_init__(self) -> None:
        if not self.provider or not self.model or not self.version or self.dimensions < 1:
            raise ValueError("embedding model identity and dimensions must be valid")

    @property
    def identity(self) -> str:
        return json.dumps(
            {
                "provider": self.provider,
                "model": self.model,
                "dimensions": self.dimensions,
                "normalized": self.normalized,
                "version": self.version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def cache_key(self, content: str) -> str:
        digest = hashlib.sha256(content.encode()).hexdigest()
        return hashlib.sha256(f"{self.identity}:{digest}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class EmbeddingUsage:
    requests: int
    input_tokens: int


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: tuple[Vector, ...]
    input_tokens: int


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: tuple[Vector, ...]
    usage: EmbeddingUsage
    model: EmbeddingModelInfo


class EmbeddingTransportError(RuntimeError):
    """Safe network-boundary error with explicit retry classification."""

    def __init__(self, *, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__("embedding provider failed")


class EmbeddingTransport(Protocol):
    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch: ...


class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: tuple[str, ...]) -> EmbeddingResult: ...

    async def embed_query(self, text: str) -> Vector: ...
