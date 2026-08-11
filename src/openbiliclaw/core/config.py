"""Frozen target-runtime settings loaded before component construction."""

from __future__ import annotations

import os
import tomllib
from typing import TYPE_CHECKING, Annotated, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

from pydantic import ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from openbiliclaw.core._pydantic import StrictBaseModel

ConfigValue: TypeAlias = JsonValue
SecretReference: TypeAlias = Annotated[str, Field(pattern=r"^[a-z][a-z0-9+.-]*:[^\s]+$")]
_CONFIG_ADAPTER = TypeAdapter(dict[str, JsonValue])


class _FrozenModel(StrictBaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelSettings(_FrozenModel):
    provider: str = Field(default="ollama", min_length=1)
    model_name: str = Field(default="", max_length=200)
    credential_ref: SecretReference | None = None


class AccessSettings(_FrozenModel):
    method: Literal["anonymous", "manual"] = "anonymous"
    credential_ref: SecretReference | None = None

    @model_validator(mode="after")
    def require_manual_credential(self) -> AccessSettings:
        if self.method == "manual" and self.credential_ref is None:
            raise ValueError("manual access requires credential_ref")
        return self


class ContentProviderSettings(_FrozenModel):
    enabled: tuple[str, ...] = ()


class RecommendationSettings(_FrozenModel):
    pool_target_count: int = Field(default=100, ge=1, le=10_000)


class HostSettings(_FrozenModel):
    api_host: str = Field(default="127.0.0.1", min_length=1)
    api_port: int = Field(default=8420, ge=1, le=65_535)


class RuntimeSettings(_FrozenModel):
    default_timeout_seconds: float = Field(default=30.0, gt=0)
    default_resource_limit: int = Field(default=4, ge=1)


class AppSettings(_FrozenModel):
    """Validated configuration root containing references, never secret values."""

    model: ModelSettings = ModelSettings()
    access: AccessSettings = AccessSettings()
    content: ContentProviderSettings = ContentProviderSettings()
    recommendation: RecommendationSettings = RecommendationSettings()
    host: HostSettings = HostSettings()
    runtime: RuntimeSettings = RuntimeSettings()

    def diagnostic_dump(self) -> dict[str, ConfigValue]:
        """Serialize settings while hiding even credential-vault identifiers."""
        values = _CONFIG_ADAPTER.validate_json(self.model_dump_json())
        return _redact_references(values)


class ModelOverrides(_FrozenModel):
    provider: str | None = None
    model_name: str | None = None
    credential_ref: SecretReference | None = None


class AccessOverrides(_FrozenModel):
    method: Literal["anonymous", "manual"] | None = None
    credential_ref: SecretReference | None = None


class ContentOverrides(_FrozenModel):
    enabled: tuple[str, ...] | None = None


class RecommendationOverrides(_FrozenModel):
    pool_target_count: int | None = None


class HostOverrides(_FrozenModel):
    api_host: str | None = None
    api_port: int | None = None


class RuntimeOverrides(_FrozenModel):
    default_timeout_seconds: float | None = None
    default_resource_limit: int | None = None


class SettingsOverrides(_FrozenModel):
    """Typed CLI overrides applied after file and environment values."""

    model: ModelOverrides | None = None
    access: AccessOverrides | None = None
    content: ContentOverrides | None = None
    recommendation: RecommendationOverrides | None = None
    host: HostOverrides | None = None
    runtime: RuntimeOverrides | None = None


def _redact_references(values: dict[str, ConfigValue]) -> dict[str, ConfigValue]:
    redacted: dict[str, ConfigValue] = {}
    for key, value in values.items():
        if key.endswith("credential_ref") and value is not None:
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
        ("OPENBILICLAW_MODEL_CREDENTIAL_REF", "model", "credential_ref"),
        ("OPENBILICLAW_ACCESS_METHOD", "access", "method"),
        ("OPENBILICLAW_ACCESS_CREDENTIAL_REF", "access", "credential_ref"),
        ("OPENBILICLAW_API_HOST", "host", "api_host"),
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
        if overrides.model.credential_ref is not None:
            _set(values, "model", "credential_ref", overrides.model.credential_ref)
    if overrides.access is not None:
        if overrides.access.method is not None:
            _set(values, "access", "method", overrides.access.method)
        if overrides.access.credential_ref is not None:
            _set(values, "access", "credential_ref", overrides.access.credential_ref)
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
