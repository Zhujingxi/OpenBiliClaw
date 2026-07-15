"""Typed model configuration domain model, registry, and validation."""

from .registry import (
    ConnectionCapability,
    ConnectionCategory,
    ConnectionTypeDefinition,
    ConnectionTypeRegistry,
    FieldDefinition,
    PresetDefinition,
    apply_preset_defaults,
    connection_type_registry,
)
from .types import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    CredentialSource,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
    IssueSeverity,
    ModelConfig,
    ModelConfigIssue,
)
from .validation import validate_model_config


def default_model_config() -> ModelConfig:
    """Return the editable first-run model route without any embedded secret."""
    return ModelConfig(
        schema_version=1,
        chat=ChatRouteConfig(
            connections=(
                ChatConnection(
                    id="deepseek-main",
                    name="DeepSeek Flash",
                    type="openai_compatible",
                    preset="deepseek",
                    model="deepseek-v4-flash",
                    base_url="https://api.deepseek.com",
                    api_mode="chat_completions",
                    reasoning_effort="max",
                ),
            ),
            concurrency=4,
            timeout_seconds=300,
        ),
        embedding=EmbeddingRouteConfig(
            enabled=False,
            settings=EmbeddingModelSettings(model="bge-m3"),
            providers=(),
        ),
    )


__all__ = [
    "ChatConnection",
    "ChatRouteConfig",
    "ConnectionCapability",
    "ConnectionCategory",
    "ConnectionTypeDefinition",
    "ConnectionTypeRegistry",
    "CredentialConfig",
    "CredentialSource",
    "EmbeddingModelSettings",
    "EmbeddingProviderConfig",
    "EmbeddingRouteConfig",
    "FieldDefinition",
    "IssueSeverity",
    "ModelConfig",
    "ModelConfigIssue",
    "PresetDefinition",
    "apply_preset_defaults",
    "connection_type_registry",
    "default_model_config",
    "validate_model_config",
]
