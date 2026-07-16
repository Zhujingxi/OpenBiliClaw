"""Typed model configuration domain model, registry, and validation."""

from .migration import (
    LegacyMigrationResult,
    MigrationAction,
    MigrationIssue,
    MigrationReport,
    MigrationResolution,
    MigrationResolutionError,
    apply_migration_resolutions,
    legacy_connection_id,
    migrate_legacy_llm,
)
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
from .revision import compute_model_revision
from .serialization import ModelConfigParseError, parse_model_config, render_model_config
from .service import (
    CredentialAction,
    CredentialActionName,
    ModelConfigFieldError,
    ModelConfigOverride,
    ModelConfigProbeResult,
    ModelConfigSaveRequest,
    ModelConfigSaveResult,
    ModelConfigService,
    ModelConfigSnapshot,
    ModelConfigValidationError,
    ModelRuntimeCoordinator,
    PublicCredentialStatus,
    PublicModelConfig,
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
                    credential=CredentialConfig(source="env", value="DEEPSEEK_API_KEY"),
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
    "CredentialAction",
    "CredentialActionName",
    "CredentialSource",
    "EmbeddingModelSettings",
    "EmbeddingProviderConfig",
    "EmbeddingRouteConfig",
    "FieldDefinition",
    "IssueSeverity",
    "LegacyMigrationResult",
    "MigrationAction",
    "MigrationIssue",
    "MigrationReport",
    "MigrationResolution",
    "MigrationResolutionError",
    "ModelConfig",
    "ModelConfigFieldError",
    "ModelConfigIssue",
    "ModelConfigOverride",
    "ModelConfigParseError",
    "ModelConfigProbeResult",
    "ModelConfigSaveRequest",
    "ModelConfigSaveResult",
    "ModelConfigService",
    "ModelConfigSnapshot",
    "ModelConfigValidationError",
    "ModelRuntimeCoordinator",
    "PresetDefinition",
    "PublicCredentialStatus",
    "PublicModelConfig",
    "apply_preset_defaults",
    "apply_migration_resolutions",
    "connection_type_registry",
    "compute_model_revision",
    "default_model_config",
    "legacy_connection_id",
    "migrate_legacy_llm",
    "parse_model_config",
    "render_model_config",
    "validate_model_config",
]
