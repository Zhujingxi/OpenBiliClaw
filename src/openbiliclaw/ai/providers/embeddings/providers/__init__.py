"""Provider-specific typed embedding transports."""

from .http import (
    EmbeddingProviderKind,
    EmbeddingTransportConfig,
    GoogleEmbeddingTransport,
    OllamaEmbeddingTransport,
    OpenAIEmbeddingTransport,
    build_embedding_transport,
)

__all__ = [
    "EmbeddingProviderKind",
    "EmbeddingTransportConfig",
    "GoogleEmbeddingTransport",
    "OllamaEmbeddingTransport",
    "OpenAIEmbeddingTransport",
    "build_embedding_transport",
]
