"""Typed embedding execution separate from chat models."""

from .index import EmbeddingIndex, EmbeddingKind, EmbeddingMatch
from .protocol import (
    EmbeddingBatch,
    EmbeddingModelInfo,
    EmbeddingProvider,
    EmbeddingResult,
    EmbeddingTransport,
    EmbeddingTransportError,
    EmbeddingUsage,
    Vector,
)
from .service import EmbeddingService

__all__ = [
    "EmbeddingBatch",
    "EmbeddingIndex",
    "EmbeddingKind",
    "EmbeddingMatch",
    "EmbeddingModelInfo",
    "EmbeddingProvider",
    "EmbeddingResult",
    "EmbeddingService",
    "EmbeddingTransport",
    "EmbeddingTransportError",
    "EmbeddingUsage",
    "Vector",
]
