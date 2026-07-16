"""Structural validation for immutable model configuration values."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .endpoints import InvalidModelEndpointError, validated_native_base_url
from .types import CredentialConfig, ModelConfig, ModelConfigIssue

if TYPE_CHECKING:
    from .registry import ConnectionTypeDefinition, ConnectionTypeRegistry

_VALID_CREDENTIAL_SOURCES = frozenset({"none", "inline", "env", "oauth"})
_COMMON_CONNECTION_FIELDS = frozenset({"id", "name", "type"})


def _issue(
    path: str,
    code: str,
    message: str,
    connection_id: str | None = None,
) -> ModelConfigIssue:
    return ModelConfigIssue(
        path=path,
        code=code,
        message=message,
        severity="blocking",
        connection_id=connection_id,
    )


def _value(record: object, name: str, default: object = "") -> object:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _text(record: object, name: str) -> str:
    value = _value(record, name)
    return value if isinstance(value, str) else str(value)


def _is_populated(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, CredentialConfig):
        return value.source != "none" or bool(value.value.strip())
    if isinstance(value, Mapping):
        source = value.get("source", "none")
        secret = value.get("value", "")
        return source != "none" or bool(str(secret).strip())
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return value is not None


def _credential(record: object) -> CredentialConfig | Mapping[str, object]:
    value = _value(record, "credential", CredentialConfig())
    if isinstance(value, CredentialConfig | Mapping):
        return value
    return {"source": value}


def _credential_source(credential: CredentialConfig | Mapping[str, object]) -> object:
    if isinstance(credential, Mapping):
        return credential.get("source", "none")
    return credential.source


def _credential_value(credential: CredentialConfig | Mapping[str, object]) -> str:
    value = credential.get("value", "") if isinstance(credential, Mapping) else credential.value
    return value if isinstance(value, str) else str(value)


def _validate_credential(
    record: object,
    *,
    path: str,
    connection_id: str | None,
    definition: ConnectionTypeDefinition,
    allowed_fields: frozenset[str],
) -> list[ModelConfigIssue]:
    issues: list[ModelConfigIssue] = []
    credential = _credential(record)
    source = _credential_source(credential)
    value = _credential_value(credential)
    credential_path = f"{path}.credential"
    if source not in _VALID_CREDENTIAL_SOURCES:
        issues.append(
            _issue(
                f"{credential_path}.source",
                "invalid_credential_source",
                "Credential source must be none, inline, env, or oauth.",
                connection_id,
            )
        )
        return issues

    if source == "none" and value.strip():
        issues.append(
            _issue(
                credential_path,
                "invalid_credential_value",
                "A credential value requires an explicit credential source.",
                connection_id,
            )
        )
    elif source in {"inline", "env", "oauth"} and not value.strip():
        issues.append(
            _issue(
                credential_path,
                "invalid_credential_value",
                "The selected credential source requires a value or reference.",
                connection_id,
            )
        )

    credential_required = any(
        field.name == "credential" and field.required for field in definition.fields
    )
    if credential_required and "credential" in allowed_fields and source == "none":
        issues.append(
            _issue(
                credential_path,
                "required_connection_field",
                "This connection type requires a credential source.",
                connection_id,
            )
        )

    if definition.id == "codex_oauth" and (source != "oauth" or value.strip() != "codex"):
        issues.append(
            _issue(
                credential_path,
                "invalid_oauth_reference",
                "Codex OAuth connections must reference the imported codex credential.",
                connection_id,
            )
        )
    elif definition.id != "codex_oauth" and source == "oauth":
        issues.append(
            _issue(
                credential_path,
                "invalid_oauth_reference",
                "OAuth credentials are only valid for an OAuth connection type.",
                connection_id,
            )
        )
    return issues


def _validate_record(
    record: object,
    *,
    path: str,
    capability: str,
    registry: ConnectionTypeRegistry,
) -> list[ModelConfigIssue]:
    issues: list[ModelConfigIssue] = []
    raw_id = _text(record, "id")
    connection_id = raw_id.strip() or None
    connection_type = _text(record, "type").strip()
    preset = _text(record, "preset").strip()
    definition = registry.get(connection_type)
    if definition is None:
        issues.append(
            _issue(
                f"{path}.type",
                "unknown_connection_type",
                "Connection type is not registered.",
                connection_id,
            )
        )
        return issues

    try:
        validated_native_base_url(_text(record, "base_url"))
    except InvalidModelEndpointError:
        issues.append(
            _issue(
                f"{path}.base_url",
                "invalid_endpoint",
                "Base URL must be a safe HTTP or HTTPS endpoint.",
                connection_id,
            )
        )

    if capability not in definition.capabilities:
        issues.append(
            _issue(
                f"{path}.type",
                "unsupported_capability",
                f"Connection type does not support {capability} routes.",
                connection_id,
            )
        )

    available_presets = registry.presets_for(connection_type, capability)
    if definition.presets and preset not in available_presets:
        issues.append(
            _issue(
                f"{path}.preset",
                "unknown_preset",
                "Preset is not available for this connection type and route.",
                connection_id,
            )
        )
    elif not definition.presets and preset:
        issues.append(
            _issue(
                f"{path}.preset",
                "unknown_preset",
                "This connection type does not define presets.",
                connection_id,
            )
        )

    allowed_fields = frozenset(
        (*_COMMON_CONNECTION_FIELDS, *definition.allowed_fields(capability, preset))
    )
    candidate_fields = set(record) if isinstance(record, Mapping) else set(vars(record))
    for field_name in sorted(candidate_fields - allowed_fields):
        if capability == "embedding" and field_name == "model":
            issues.append(
                _issue(
                    f"{path}.model",
                    "embedding_provider_model",
                    "Embedding model belongs to the shared route settings, not a provider.",
                    connection_id,
                )
            )
        elif _is_populated(_value(record, field_name, None)):
            issues.append(
                _issue(
                    f"{path}.{field_name}",
                    "illegal_connection_field",
                    "Field is not valid for this connection type, preset, and route.",
                    connection_id,
                )
            )

    for field_definition in definition.fields:
        if (
            field_definition.required
            and field_definition.name in allowed_fields
            and field_definition.name not in {"credential", "preset"}
            and not _is_populated(_value(record, field_definition.name))
        ):
            issues.append(
                _issue(
                    f"{path}.{field_definition.name}",
                    "required_connection_field",
                    "Required connection field is blank.",
                    connection_id,
                )
            )
        if (
            field_definition.name not in allowed_fields
            or field_definition.name == "credential"
            or not field_definition.choices
        ):
            continue
        raw_choice = _text(record, field_definition.name).strip().lower()
        allowed_choices = frozenset(
            str(choice).strip().lower() for choice in field_definition.choices
        )
        if raw_choice and raw_choice not in allowed_choices:
            issues.append(
                _issue(
                    f"{path}.{field_definition.name}",
                    "invalid_connection_field_choice",
                    "Field value is not one of the registered choices.",
                    connection_id,
                )
            )

    issues.extend(
        _validate_credential(
            record,
            path=path,
            connection_id=connection_id,
            definition=definition,
            allowed_fields=allowed_fields,
        )
    )
    return issues


def _validate_ids(records: tuple[tuple[object, str], ...]) -> list[ModelConfigIssue]:
    issues: list[ModelConfigIssue] = []
    seen: set[str] = set()
    for record, path in records:
        raw_id = _text(record, "id")
        connection_id = raw_id.strip()
        if not connection_id:
            issues.append(
                _issue(
                    f"{path}.id",
                    "blank_connection_id",
                    "Connection ID must not be blank.",
                )
            )
            continue
        if connection_id in seen:
            issues.append(
                _issue(
                    f"{path}.id",
                    "duplicate_connection_id",
                    "Connection IDs must be unique across chat and embedding routes.",
                    connection_id,
                )
            )
        seen.add(connection_id)
    return issues


def validate_model_config(
    config: ModelConfig,
    registry: ConnectionTypeRegistry,
) -> list[ModelConfigIssue]:
    """Validate structure and type-specific invariants without mutating config."""
    issues: list[ModelConfigIssue] = []
    chat_connections = tuple(config.chat.connections)
    embedding_providers: tuple[Any, ...] = tuple(config.embedding.providers)

    concurrency = config.chat.concurrency
    if (
        isinstance(concurrency, bool)
        or not isinstance(concurrency, int)
        or not 1 <= concurrency <= 16
    ):
        issues.append(
            _issue(
                "models.chat.concurrency",
                "invalid_chat_concurrency",
                "Chat concurrency must be an integer between 1 and 16.",
            )
        )

    timeout_seconds = config.chat.timeout_seconds
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds < 10
    ):
        issues.append(
            _issue(
                "models.chat.timeout_seconds",
                "invalid_chat_timeout",
                "Chat timeout must be an integer of at least 10 seconds.",
            )
        )

    output_dimensionality = config.embedding.settings.output_dimensionality
    if (
        isinstance(output_dimensionality, bool)
        or not isinstance(output_dimensionality, int)
        or output_dimensionality < 0
    ):
        issues.append(
            _issue(
                "models.embedding.settings.output_dimensionality",
                "invalid_embedding_output_dimensionality",
                "Embedding output dimensionality must be a non-negative integer.",
            )
        )

    similarity_threshold = config.embedding.settings.similarity_threshold
    if (
        isinstance(similarity_threshold, bool)
        or not isinstance(similarity_threshold, int | float)
        or not 0.0 <= similarity_threshold <= 1.0
        or not math.isfinite(similarity_threshold)
    ):
        issues.append(
            _issue(
                "models.embedding.settings.similarity_threshold",
                "invalid_embedding_similarity_threshold",
                "Embedding similarity threshold must be finite and between 0 and 1.",
            )
        )

    if not 1 <= len(chat_connections) <= 10:
        issues.append(
            _issue(
                "models.chat.connections",
                "chat_connection_count",
                "Chat routes require between one and ten ordered connections.",
            )
        )

    if config.embedding.enabled and not 1 <= len(embedding_providers) <= 10:
        issues.append(
            _issue(
                "models.embedding.providers",
                "embedding_provider_count",
                "Enabled embedding routes require between one and ten ordered providers.",
            )
        )
    elif not config.embedding.enabled and embedding_providers:
        issues.append(
            _issue(
                "models.embedding.providers",
                "embedding_disabled_with_providers",
                "Disabled embedding routes must not retain active providers.",
            )
        )

    records = tuple(
        (record, f"models.chat.connections[{index}]")
        for index, record in enumerate(chat_connections)
    ) + tuple(
        (record, f"models.embedding.providers[{index}]")
        for index, record in enumerate(embedding_providers)
    )
    issues.extend(_validate_ids(records))

    for index, connection in enumerate(chat_connections):
        num_ctx = _value(connection, "num_ctx", 0)
        if isinstance(num_ctx, bool) or not isinstance(num_ctx, int) or num_ctx < 0:
            connection_id = _text(connection, "id").strip() or None
            issues.append(
                _issue(
                    f"models.chat.connections[{index}].num_ctx",
                    "invalid_chat_num_ctx",
                    "Ollama context size must be a non-negative integer.",
                    connection_id,
                )
            )
        issues.extend(
            _validate_record(
                connection,
                path=f"models.chat.connections[{index}]",
                capability="chat",
                registry=registry,
            )
        )
    for index, provider in enumerate(embedding_providers):
        issues.extend(
            _validate_record(
                provider,
                path=f"models.embedding.providers[{index}]",
                capability="embedding",
                registry=registry,
            )
        )
    return issues
