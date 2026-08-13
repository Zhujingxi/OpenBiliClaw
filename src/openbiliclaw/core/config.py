"""Frozen target-runtime settings loaded before component construction."""

from __future__ import annotations

import os
import tomllib
from typing import TYPE_CHECKING, Annotated, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

from pydantic import ConfigDict, Field, JsonValue, TypeAdapter

from openbiliclaw.core._pydantic import StrictBaseModel

ConfigValue: TypeAlias = JsonValue
SecretReference: TypeAlias = Annotated[str, Field(pattern=r"^[a-z][a-z0-9+.-]*:[^\s]+$")]
_CONFIG_ADAPTER = TypeAdapter(dict[str, JsonValue])


class _FrozenModel(StrictBaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


ModelProviderName: TypeAlias = Literal["openai", "anthropic", "deepseek", "google", "openrouter"]


class ModelOptions(_FrozenModel):
    disable_thinking: bool = False


class ModelSettings(_FrozenModel):
    provider: ModelProviderName = "openai"
    model_name: str = Field(default="", max_length=200)
    endpoint: str | None = Field(default=None, max_length=2_000, pattern=r"^https?://")
    secret_ref: SecretReference | None = None
    options: ModelOptions = ModelOptions()


class EmbeddingSettings(ModelSettings):
    output_dimensions: int = Field(default=1_536, gt=0)


class ContentProviderSettings(_FrozenModel):
    enabled: tuple[str, ...] = ()


class RecommendationSettings(_FrozenModel):
    pool_target_count: int = Field(default=100, ge=1, le=10_000)


class HostSettings(_FrozenModel):
    api_host: str = Field(default="127.0.0.1", min_length=1)
    api_port: int = Field(default=8420, ge=1, le=65_535)
    bearer_secret_ref: SecretReference | None = None


class RuntimeSettings(_FrozenModel):
    default_timeout_seconds: float = Field(default=30.0, gt=0)
    default_resource_limit: int = Field(default=4, ge=1)


class AppSettings(_FrozenModel):
    """Validated configuration root containing references, never secret values."""

    model: ModelSettings = ModelSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    content: ContentProviderSettings = ContentProviderSettings()
    recommendation: RecommendationSettings = RecommendationSettings()
    host: HostSettings = HostSettings()
    runtime: RuntimeSettings = RuntimeSettings()

    def diagnostic_dump(self) -> dict[str, ConfigValue]:
        """Serialize settings while hiding even credential-vault identifiers."""
        values = _CONFIG_ADAPTER.validate_json(self.model_dump_json())
        return _redact_references(values)


class ModelOverrides(_FrozenModel):
    provider: ModelProviderName | None = None
    model_name: str | None = None
    endpoint: str | None = None
    secret_ref: SecretReference | None = None


class EmbeddingOverrides(ModelOverrides):
    output_dimensions: int | None = Field(default=None, gt=0)


class ContentOverrides(_FrozenModel):
    enabled: tuple[str, ...] | None = None


class RecommendationOverrides(_FrozenModel):
    pool_target_count: int | None = None


class HostOverrides(_FrozenModel):
    api_host: str | None = None
    api_port: int | None = None
    bearer_secret_ref: SecretReference | None = None


class RuntimeOverrides(_FrozenModel):
    default_timeout_seconds: float | None = None
    default_resource_limit: int | None = None


class SettingsOverrides(_FrozenModel):
    """Typed CLI overrides applied after file and environment values."""

    model: ModelOverrides | None = None
    embedding: EmbeddingOverrides | None = None
    content: ContentOverrides | None = None
    recommendation: RecommendationOverrides | None = None
    host: HostOverrides | None = None
    runtime: RuntimeOverrides | None = None


def _redact_references(values: dict[str, ConfigValue]) -> dict[str, ConfigValue]:
    redacted: dict[str, ConfigValue] = {}
    for key, value in values.items():
        if key.endswith("secret_ref") and value is not None:
            redacted[key] = "<redacted-ref>"
        elif isinstance(value, dict):
            redacted[key] = _redact_references(value)
        elif isinstance(value, list):
            redacted[key] = [_redact_value(item) for item in value]
        else:
            redacted[key] = value
    return redacted


def _redact_value(value: ConfigValue) -> ConfigValue:
    if isinstance(value, dict):
        return _redact_references(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _section(values: dict[str, ConfigValue], name: str) -> dict[str, ConfigValue]:
    existing = values.get(name)
    if existing is None:
        section: dict[str, ConfigValue] = {}
    elif isinstance(existing, dict):
        section = dict(existing)
    else:
        raise ValueError(f"configuration section {name!r} must be a table")
    values[name] = section
    return section


def _set(values: dict[str, ConfigValue], section_name: str, key: str, value: ConfigValue) -> None:
    _section(values, section_name)[key] = value


def _parse_int(environment: Mapping[str, str], key: str) -> int | None:
    raw = environment.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be an integer") from error


def _parse_float(environment: Mapping[str, str], key: str) -> float | None:
    raw = environment.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be a number") from error


def _apply_environment(values: dict[str, ConfigValue], environment: Mapping[str, str]) -> None:
    string_keys = (
        ("OPENBILICLAW_MODEL_PROVIDER", "model", "provider"),
        ("OPENBILICLAW_MODEL_NAME", "model", "model_name"),
        ("OPENBILICLAW_MODEL_ENDPOINT", "model", "endpoint"),
        ("OPENBILICLAW_MODEL_SECRET_REF", "model", "secret_ref"),
        ("OPENBILICLAW_EMBEDDING_PROVIDER", "embedding", "provider"),
        ("OPENBILICLAW_EMBEDDING_MODEL", "embedding", "model_name"),
        ("OPENBILICLAW_EMBEDDING_ENDPOINT", "embedding", "endpoint"),
        ("OPENBILICLAW_EMBEDDING_SECRET_REF", "embedding", "secret_ref"),
        ("OPENBILICLAW_API_HOST", "host", "api_host"),
        ("OPENBILICLAW_API_BEARER_SECRET_REF", "host", "bearer_secret_ref"),
    )
    for environment_key, section_name, key in string_keys:
        value = environment.get(environment_key)
        if value is not None:
            _set(values, section_name, key, value)

    enabled = environment.get("OPENBILICLAW_CONTENT_PROVIDERS")
    if enabled is not None:
        _set(
            values,
            "content",
            "enabled",
            [item.strip() for item in enabled.split(",") if item.strip()],
        )

    integer_keys = (
        ("OPENBILICLAW_API_PORT", "host", "api_port"),
        ("OPENBILICLAW_POOL_TARGET_COUNT", "recommendation", "pool_target_count"),
        ("OPENBILICLAW_DEFAULT_RESOURCE_LIMIT", "runtime", "default_resource_limit"),
        ("OPENBILICLAW_EMBEDDING_OUTPUT_DIMENSIONS", "embedding", "output_dimensions"),
    )
    for environment_key, section_name, key in integer_keys:
        integer_value = _parse_int(environment, environment_key)
        if integer_value is not None:
            _set(values, section_name, key, integer_value)

    timeout = _parse_float(environment, "OPENBILICLAW_DEFAULT_TIMEOUT_SECONDS")
    if timeout is not None:
        _set(values, "runtime", "default_timeout_seconds", timeout)


def _apply_overrides(values: dict[str, ConfigValue], overrides: SettingsOverrides) -> None:
    if overrides.model is not None:
        if overrides.model.provider is not None:
            _set(values, "model", "provider", overrides.model.provider)
        if overrides.model.model_name is not None:
            _set(values, "model", "model_name", overrides.model.model_name)
        if overrides.model.endpoint is not None:
            _set(values, "model", "endpoint", overrides.model.endpoint)
        if overrides.model.secret_ref is not None:
            _set(values, "model", "secret_ref", overrides.model.secret_ref)
    if overrides.embedding is not None:
        if overrides.embedding.provider is not None:
            _set(values, "embedding", "provider", overrides.embedding.provider)
        if overrides.embedding.model_name is not None:
            _set(values, "embedding", "model_name", overrides.embedding.model_name)
        if overrides.embedding.endpoint is not None:
            _set(values, "embedding", "endpoint", overrides.embedding.endpoint)
        if overrides.embedding.secret_ref is not None:
            _set(values, "embedding", "secret_ref", overrides.embedding.secret_ref)
        if overrides.embedding.output_dimensions is not None:
            _set(
                values,
                "embedding",
                "output_dimensions",
                overrides.embedding.output_dimensions,
            )
    if overrides.content is not None and overrides.content.enabled is not None:
        _set(values, "content", "enabled", list(overrides.content.enabled))
    if (
        overrides.recommendation is not None
        and overrides.recommendation.pool_target_count is not None
    ):
        _set(
            values,
            "recommendation",
            "pool_target_count",
            overrides.recommendation.pool_target_count,
        )
    if overrides.host is not None:
        if overrides.host.api_host is not None:
            _set(values, "host", "api_host", overrides.host.api_host)
        if overrides.host.api_port is not None:
            _set(values, "host", "api_port", overrides.host.api_port)
        if overrides.host.bearer_secret_ref is not None:
            _set(values, "host", "bearer_secret_ref", overrides.host.bearer_secret_ref)
    if overrides.runtime is not None:
        if overrides.runtime.default_timeout_seconds is not None:
            _set(
                values,
                "runtime",
                "default_timeout_seconds",
                overrides.runtime.default_timeout_seconds,
            )
        if overrides.runtime.default_resource_limit is not None:
            _set(
                values,
                "runtime",
                "default_resource_limit",
                overrides.runtime.default_resource_limit,
            )


def load_settings(
    path: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
    cli_overrides: SettingsOverrides | None = None,
) -> AppSettings:
    """Load and validate settings with file < environment < CLI precedence."""
    if path is None:
        values: dict[str, ConfigValue] = {}
    else:
        with path.open("rb") as config_file:
            raw: object = tomllib.load(config_file)
        values = _CONFIG_ADAPTER.validate_python(raw)
    _apply_environment(values, os.environ if environ is None else environ)
    if cli_overrides is not None:
        _apply_overrides(values, cli_overrides)
    return AppSettings.model_validate(values)
