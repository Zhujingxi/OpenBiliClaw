"""Strict TOML-domain parsing and deterministic rendering for ``[models]``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeAlias

from .types import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    CredentialSource,
    EmbeddingModelSettings,
    EmbeddingProviderConfig,
    EmbeddingRouteConfig,
    ModelConfig,
)

RawTable: TypeAlias = Mapping[str, object]

_MODEL_FIELDS = frozenset({"schema_version", "chat", "embedding"})
_CHAT_FIELDS = frozenset({"connections", "concurrency", "timeout_seconds"})
_CHAT_CONNECTION_FIELDS = frozenset(
    {
        "id",
        "name",
        "type",
        "model",
        "preset",
        "base_url",
        "api_key",
        "api_key_env",
        "credential_ref",
        "api_mode",
        "reasoning_effort",
        "http_referer",
        "x_title",
        "num_ctx",
    }
)
_EMBEDDING_FIELDS = frozenset({"enabled", "settings", "providers"})
_EMBEDDING_SETTINGS_FIELDS = frozenset(
    {
        "model",
        "output_dimensionality",
        "similarity_threshold",
        "multimodal_enabled",
    }
)
_EMBEDDING_PROVIDER_FIELDS = frozenset(
    {
        "id",
        "name",
        "type",
        "preset",
        "base_url",
        "api_key",
        "api_key_env",
        "credential_ref",
    }
)
_CREDENTIAL_KEYS: tuple[tuple[str, CredentialSource], ...] = (
    ("api_key", "inline"),
    ("api_key_env", "env"),
    ("credential_ref", "oauth"),
)


class ModelConfigParseError(ValueError):
    """A secret-safe structural error in a native ``[models]`` table."""


def _error(path: str, message: str) -> ModelConfigParseError:
    return ModelConfigParseError(f"{path}: {message}")


def _table(value: object, path: str) -> RawTable:
    if not isinstance(value, Mapping):
        raise _error(path, "expected a table")
    for key in value:
        if not isinstance(key, str):
            raise _error(path, "table keys must be strings")
    return value


def _reject_unknown(raw: RawTable, allowed: frozenset[str], path: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _error(f"{path}.{unknown[0]}", "unknown field")


def _text(raw: RawTable, name: str, path: str, *, default: str = "") -> str:
    if name not in raw:
        return default
    value = raw[name]
    if not isinstance(value, str):
        raise _error(f"{path}.{name}", "expected a string")
    return value


def _enum_text(raw: RawTable, name: str, path: str, *, default: str = "") -> str:
    return _text(raw, name, path, default=default).strip().lower()


def _integer(raw: RawTable, name: str, path: str, *, default: int) -> int:
    if name not in raw:
        return default
    value = raw[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(f"{path}.{name}", "expected an integer")
    return value


def _number(raw: RawTable, name: str, path: str, *, default: float) -> float:
    if name not in raw:
        return default
    value = raw[name]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _error(f"{path}.{name}", "expected a number")
    return float(value)


def _boolean(raw: RawTable, name: str, path: str, *, default: bool) -> bool:
    if name not in raw:
        return default
    value = raw[name]
    if not isinstance(value, bool):
        raise _error(f"{path}.{name}", "expected a boolean")
    return value


def _records(raw: RawTable, name: str, path: str) -> tuple[RawTable, ...]:
    if name not in raw:
        return ()
    value = raw[name]
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise _error(f"{path}.{name}", "expected an array of tables")
    return tuple(_table(item, f"{path}.{name}[{index}]") for index, item in enumerate(value))


def _credential(raw: RawTable, path: str) -> CredentialConfig:
    selected = [(key, source) for key, source in _CREDENTIAL_KEYS if key in raw]
    if len(selected) > 1:
        raise _error(f"{path}.credential", "multiple credential sources are not allowed")
    if not selected:
        return CredentialConfig()
    key, source = selected[0]
    return CredentialConfig(source=source, value=_text(raw, key, path))


def _chat_connection(raw: RawTable, index: int) -> ChatConnection:
    path = f"models.chat.connections[{index}]"
    _reject_unknown(raw, _CHAT_CONNECTION_FIELDS, path)
    return ChatConnection(
        id=_text(raw, "id", path),
        name=_text(raw, "name", path),
        type=_enum_text(raw, "type", path),
        model=_text(raw, "model", path),
        preset=_enum_text(raw, "preset", path),
        base_url=_text(raw, "base_url", path),
        credential=_credential(raw, path),
        api_mode=_enum_text(raw, "api_mode", path),
        reasoning_effort=_enum_text(raw, "reasoning_effort", path),
        http_referer=_text(raw, "http_referer", path),
        x_title=_text(raw, "x_title", path),
        num_ctx=_integer(raw, "num_ctx", path, default=0),
    )


def _embedding_provider(raw: RawTable, index: int) -> EmbeddingProviderConfig:
    path = f"models.embedding.providers[{index}]"
    _reject_unknown(raw, _EMBEDDING_PROVIDER_FIELDS, path)
    return EmbeddingProviderConfig(
        id=_text(raw, "id", path),
        name=_text(raw, "name", path),
        type=_enum_text(raw, "type", path),
        preset=_enum_text(raw, "preset", path),
        base_url=_text(raw, "base_url", path),
        credential=_credential(raw, path),
    )


def parse_model_config(raw: Mapping[str, object]) -> ModelConfig:
    """Parse one native ``[models]`` table without accepting stale fields.

    The function performs structural parsing only. Semantic constraints such as
    registered connection types and required credentials remain the concern of
    :func:`validate_model_config`.
    """
    models = _table(raw, "models")
    _reject_unknown(models, _MODEL_FIELDS, "models")
    schema_version = _integer(models, "schema_version", "models", default=0)
    if schema_version != 1:
        raise _error("models.schema_version", "only schema version 1 is supported")

    chat = _table(models.get("chat", {}), "models.chat")
    _reject_unknown(chat, _CHAT_FIELDS, "models.chat")
    chat_records = _records(chat, "connections", "models.chat")

    embedding = _table(models.get("embedding", {}), "models.embedding")
    _reject_unknown(embedding, _EMBEDDING_FIELDS, "models.embedding")
    settings = _table(embedding.get("settings", {}), "models.embedding.settings")
    _reject_unknown(settings, _EMBEDDING_SETTINGS_FIELDS, "models.embedding.settings")
    provider_records = _records(embedding, "providers", "models.embedding")

    return ModelConfig(
        schema_version=1,
        chat=ChatRouteConfig(
            connections=tuple(
                _chat_connection(record, index) for index, record in enumerate(chat_records)
            ),
            concurrency=_integer(chat, "concurrency", "models.chat", default=4),
            timeout_seconds=_integer(chat, "timeout_seconds", "models.chat", default=300),
        ),
        embedding=EmbeddingRouteConfig(
            enabled=_boolean(embedding, "enabled", "models.embedding", default=False),
            settings=EmbeddingModelSettings(
                model=_text(settings, "model", "models.embedding.settings"),
                output_dimensionality=_integer(
                    settings,
                    "output_dimensionality",
                    "models.embedding.settings",
                    default=1024,
                ),
                similarity_threshold=_number(
                    settings,
                    "similarity_threshold",
                    "models.embedding.settings",
                    default=0.82,
                ),
                multimodal_enabled=_boolean(
                    settings,
                    "multimodal_enabled",
                    "models.embedding.settings",
                    default=False,
                ),
            ),
            providers=tuple(
                _embedding_provider(record, index) for index, record in enumerate(provider_records)
            ),
        ),
    )


def encode_toml_basic_string(value: str) -> str:
    """Encode one TOML basic string without emitting forbidden controls."""
    short_escapes = {
        "\b": "\\b",
        "\t": "\\t",
        "\n": "\\n",
        "\f": "\\f",
        "\r": "\\r",
        '"': '\\"',
        "\\": "\\\\",
    }
    encoded = ['"']
    for char in value:
        escaped = short_escapes.get(char)
        if escaped is not None:
            encoded.append(escaped)
            continue
        codepoint = ord(char)
        if codepoint <= 0x1F or codepoint == 0x7F:
            encoded.append(f"\\u{codepoint:04X}")
            continue
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("TOML strings require Unicode scalar values")
        encoded.append(char)
    encoded.append('"')
    return "".join(encoded)


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_number(value: float) -> str:
    return repr(float(value)).lower()


def _append_optional(lines: list[str], name: str, value: str) -> None:
    if value:
        lines.append(f"{name} = {encode_toml_basic_string(value)}")


def _append_credential(lines: list[str], credential: CredentialConfig) -> None:
    if not credential.value:
        return
    key = {
        "inline": "api_key",
        "env": "api_key_env",
        "oauth": "credential_ref",
    }.get(credential.source)
    if key is None:
        if credential.source == "none":
            return
        raise ValueError("models credential source is not supported")
    lines.append(f"{key} = {encode_toml_basic_string(credential.value)}")


def _render_chat_connection(connection: ChatConnection) -> list[str]:
    lines = [
        "[[models.chat.connections]]",
        f"id = {encode_toml_basic_string(connection.id)}",
        f"name = {encode_toml_basic_string(connection.name)}",
        f"type = {encode_toml_basic_string(connection.type.strip().lower())}",
        f"model = {encode_toml_basic_string(connection.model)}",
    ]
    _append_optional(lines, "preset", connection.preset.strip().lower())
    _append_optional(lines, "base_url", connection.base_url)
    _append_credential(lines, connection.credential)
    _append_optional(lines, "api_mode", connection.api_mode.strip().lower())
    _append_optional(lines, "reasoning_effort", connection.reasoning_effort.strip().lower())
    _append_optional(lines, "http_referer", connection.http_referer)
    _append_optional(lines, "x_title", connection.x_title)
    if connection.num_ctx:
        lines.append(f"num_ctx = {connection.num_ctx}")
    return lines


def _render_embedding_provider(provider: EmbeddingProviderConfig) -> list[str]:
    lines = [
        "[[models.embedding.providers]]",
        f"id = {encode_toml_basic_string(provider.id)}",
        f"name = {encode_toml_basic_string(provider.name)}",
        f"type = {encode_toml_basic_string(provider.type.strip().lower())}",
    ]
    _append_optional(lines, "preset", provider.preset.strip().lower())
    _append_optional(lines, "base_url", provider.base_url)
    _append_credential(lines, provider.credential)
    return lines


def render_model_config(config: ModelConfig) -> list[str]:
    """Render a native model configuration in one stable TOML order."""
    if type(config.schema_version) is not int or config.schema_version != 1:
        raise ValueError("models.schema_version: only schema version 1 is supported")

    lines = [
        "[models]",
        "schema_version = 1",
        "",
        "[models.chat]",
        f"concurrency = {config.chat.concurrency}",
        f"timeout_seconds = {config.chat.timeout_seconds}",
    ]
    for connection in config.chat.connections:
        lines.extend(("", *_render_chat_connection(connection)))

    lines.extend(
        (
            "",
            "[models.embedding]",
            f"enabled = {_toml_bool(config.embedding.enabled)}",
            "",
            "[models.embedding.settings]",
            f"model = {encode_toml_basic_string(config.embedding.settings.model)}",
            f"output_dimensionality = {config.embedding.settings.output_dimensionality}",
            "similarity_threshold = "
            f"{_toml_number(config.embedding.settings.similarity_threshold)}",
            f"multimodal_enabled = {_toml_bool(config.embedding.settings.multimodal_enabled)}",
        )
    )
    for provider in config.embedding.providers:
        lines.extend(("", *_render_embedding_provider(provider)))
    return lines
