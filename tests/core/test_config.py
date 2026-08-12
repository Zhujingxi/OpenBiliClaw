from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from openbiliclaw.core.config import (
    AppSettings,
    ContentOverrides,
    HostOverrides,
    ModelOverrides,
    RecommendationOverrides,
    RuntimeOverrides,
    SettingsOverrides,
    load_settings,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_app_settings_are_frozen_and_reject_unknown_fields() -> None:
    settings = AppSettings()

    with pytest.raises(ValidationError):
        settings.host = settings.host.model_copy(update={"api_port": 9000})
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"host": {"api_port": 8420, "extra": True}})


def test_settings_reject_secrets_but_accept_secret_references() -> None:
    settings = AppSettings.model_validate(
        {"model": {"provider": "openai", "credential_ref": "vault:primary"}}
    )

    assert settings.model.credential_ref == "vault:primary"
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"model": {"api_key": "secret"}})
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"model": {"credential_ref": "raw-secret"}})


def test_load_settings_applies_file_environment_then_cli_precedence(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[model]
provider = "file-provider"
model_name = "file-model"
[host]
api_port = 7000
[runtime]
default_timeout_seconds = 20
""".strip(),
        encoding="utf-8",
    )
    environment = {
        "OPENBILICLAW_MODEL_PROVIDER": "env-provider",
        "OPENBILICLAW_API_PORT": "8000",
        "OPENBILICLAW_DEFAULT_TIMEOUT_SECONDS": "30",
    }
    overrides = SettingsOverrides(
        model=ModelOverrides(provider="cli-provider"),
        host=HostOverrides(api_port=9000),
        runtime=RuntimeOverrides(default_timeout_seconds=40),
    )

    settings = load_settings(path, environ=environment, cli_overrides=overrides)

    assert settings.model.provider == "cli-provider"
    assert settings.model.model_name == "file-model"
    assert settings.host.api_port == 9000
    assert settings.runtime.default_timeout_seconds == 40


def test_load_settings_rejects_unknown_toml_and_invalid_environment(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.toml"
    unknown.write_text("[mystery]\nenabled = true\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_settings(unknown, environ={})
    with pytest.raises(ValueError, match="OPENBILICLAW_API_PORT"):
        load_settings(None, environ={"OPENBILICLAW_API_PORT": "not-a-port"})


def test_all_environment_and_override_fields_are_applied() -> None:
    environment = {
        "OPENBILICLAW_MODEL_NAME": "env-model",
        "OPENBILICLAW_MODEL_CREDENTIAL_REF": "vault:env-model",
        "OPENBILICLAW_API_HOST": "0.0.0.0",
        "OPENBILICLAW_CONTENT_PROVIDERS": "bilibili, youtube",
        "OPENBILICLAW_POOL_TARGET_COUNT": "250",
        "OPENBILICLAW_DEFAULT_RESOURCE_LIMIT": "8",
    }
    overrides = SettingsOverrides(
        model=ModelOverrides(model_name="cli-model", credential_ref="vault:cli-model"),
        content=ContentOverrides(enabled=("bilibili",)),
        recommendation=RecommendationOverrides(pool_target_count=300),
        host=HostOverrides(api_host="localhost"),
        runtime=RuntimeOverrides(default_resource_limit=6),
    )

    settings = load_settings(None, environ=environment, cli_overrides=overrides)

    assert settings.model.model_name == "cli-model"
    assert settings.content.enabled == ("bilibili",)
    assert settings.recommendation.pool_target_count == 300
    assert settings.host.api_host == "localhost"
    assert settings.runtime.default_resource_limit == 6


def test_settings_reject_cross_field_and_section_shape_errors(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.toml"
    malformed.write_text('model = "not-a-table"', encoding="utf-8")

    with pytest.raises(ValueError, match="must be a table"):
        load_settings(
            malformed,
            environ={"OPENBILICLAW_MODEL_PROVIDER": "openai"},
        )
    with pytest.raises(ValidationError, match="access"):
        AppSettings.model_validate({"access": {"method": "manual"}})
    with pytest.raises(ValueError, match="DEFAULT_TIMEOUT_SECONDS"):
        load_settings(
            None,
            environ={"OPENBILICLAW_DEFAULT_TIMEOUT_SECONDS": "invalid"},
        )


def test_diagnostic_dump_redacts_secret_references() -> None:
    settings = AppSettings.model_validate({"model": {"credential_ref": "vault:model-primary"}})

    diagnostic = settings.diagnostic_dump()
    model_diagnostic = diagnostic["model"]

    assert isinstance(model_diagnostic, dict)
    assert model_diagnostic["credential_ref"] == "<redacted-ref>"
    assert "vault:" not in repr(diagnostic)
