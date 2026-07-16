"""Secret-safe deterministic revisions for model configuration values."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import CredentialConfig, ModelConfig


def _normalized_enum(value: str) -> str:
    return value.strip().lower()


def _credential_revision(credential: CredentialConfig) -> dict[str, str]:
    fingerprint = hashlib.sha256(credential.value.encode("utf-8")).hexdigest()
    return {
        "source": credential.source,
        "fingerprint": fingerprint,
    }


def _normalized_payload(config: ModelConfig) -> dict[str, Any]:
    return {
        "schema_version": config.schema_version,
        "chat": {
            "concurrency": config.chat.concurrency,
            "timeout_seconds": config.chat.timeout_seconds,
            "connections": [
                {
                    "id": connection.id,
                    "name": connection.name,
                    "type": _normalized_enum(connection.type),
                    "model": connection.model,
                    "preset": _normalized_enum(connection.preset),
                    "base_url": connection.base_url,
                    "credential": _credential_revision(connection.credential),
                    "api_mode": _normalized_enum(connection.api_mode),
                    "reasoning_effort": _normalized_enum(connection.reasoning_effort),
                    "http_referer": connection.http_referer,
                    "x_title": connection.x_title,
                    "num_ctx": connection.num_ctx,
                }
                for connection in config.chat.connections
            ],
        },
        "embedding": {
            "enabled": config.embedding.enabled,
            "settings": {
                "model": config.embedding.settings.model,
                "output_dimensionality": config.embedding.settings.output_dimensionality,
                "similarity_threshold": config.embedding.settings.similarity_threshold,
                "multimodal_enabled": config.embedding.settings.multimodal_enabled,
            },
            "providers": [
                {
                    "id": provider.id,
                    "name": provider.name,
                    "type": _normalized_enum(provider.type),
                    "preset": _normalized_enum(provider.preset),
                    "base_url": provider.base_url,
                    "credential": _credential_revision(provider.credential),
                }
                for provider in config.embedding.providers
            ],
        },
    }


def compute_model_revision(config: ModelConfig) -> str:
    """Hash normalized values while replacing every credential with a digest."""
    encoded = json.dumps(
        _normalized_payload(config),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
