"""Embedding construction through the shared native model-provider config."""

from .native import NativeEmbeddingTransport, build_embedding_transport

__all__ = ["NativeEmbeddingTransport", "build_embedding_transport"]
