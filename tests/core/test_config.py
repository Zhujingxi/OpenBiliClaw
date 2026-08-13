from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from openbiliclaw.core.config import (
    AppSettings,
    EmbeddingOverrides,
    ModelOverrides,
    SettingsOverrides,
    load_settings,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_settings_are_frozen_and_unknown_fields_fail() -> None:
    settings = AppSettings()
    with pytest.raises(ValidationError):
        settings.host = settings.host.model_copy(update={"api_port": 9000})
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"model": {"provider": "unknown"}})
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"model": {"api_key": "secret"}})


def test_model_and_embedding_share_native_provider_shape() -> None:
    settings = AppSettings.model_validate(
        {
            "model": {
                "provider": "anthropic",
                "model_name": "claude",
                "secret_ref": "vault:chat",
                "options": {"disable_thinking": True},
            },
            "embedding": {
                "provider": "openai",
                "model_name": "embed",
                "endpoint": "https://proxy.example/v1",
                "secret_ref": "vault:embed",
                "output_dimensions": 1024,
            },
            "host": {"bearer_secret_ref": "vault:host"},
        }
    )
    assert settings.model.provider == "anthropic"
    assert settings.model.options.disable_thinking is True
    assert settings.embedding.provider == "openai"
    assert settings.embedding.output_dimensions == 1024
    assert settings.host.bearer_secret_ref == "vault:host"
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"model": {"secret_ref": "raw-secret"}})


def test_file_environment_and_cli_precedence(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[model]\nprovider="openai"\nmodel_name="file"\n[host]\napi_port=7000',
        encoding="utf-8",
    )
    settings = load_settings(
        path,
        environ={
            "OPENBILICLAW_MODEL_PROVIDER": "google",
            "OPENBILICLAW_MODEL_NAME": "env",
            "OPENBILICLAW_EMBEDDING_PROVIDER": "openai",
            "OPENBILICLAW_EMBEDDING_MODEL": "embed",
            "OPENBILICLAW_EMBEDDING_SECRET_REF": "vault:embed",
            "OPENBILICLAW_EMBEDDING_OUTPUT_DIMENSIONS": "768",
            "OPENBILICLAW_API_BEARER_SECRET_REF": "vault:host",
        },
        cli_overrides=SettingsOverrides(
            model=ModelOverrides(provider="anthropic", model_name="cli"),
            embedding=EmbeddingOverrides(model_name="cli-embed", output_dimensions=512),
        ),
    )
    assert settings.model.provider == "anthropic"
    assert settings.model.model_name == "cli"
    assert settings.embedding.model_name == "cli-embed"
    assert settings.embedding.secret_ref == "vault:embed"
    assert settings.embedding.output_dimensions == 512
    assert settings.host.bearer_secret_ref == "vault:host"


def test_load_rejects_invalid_environment_and_section_shape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="API_PORT"):
        load_settings(None, environ={"OPENBILICLAW_API_PORT": "bad"})
    malformed = tmp_path / "bad.toml"
    malformed.write_text('model="bad"', encoding="utf-8")
    with pytest.raises(ValueError, match="table"):
        load_settings(malformed, environ={"OPENBILICLAW_MODEL_PROVIDER": "openai"})


def test_diagnostics_redact_both_secret_references() -> None:
    diagnostic = AppSettings.model_validate(
        {
            "model": {"secret_ref": "vault:chat"},
            "embedding": {"secret_ref": "vault:embed"},
            "host": {"bearer_secret_ref": "vault:host"},
        }
    ).diagnostic_dump()
    model = diagnostic["model"]
    embedding = diagnostic["embedding"]
    host = diagnostic["host"]
    assert isinstance(model, dict)
    assert isinstance(embedding, dict)
    assert isinstance(host, dict)
    assert model["secret_ref"] == "<redacted-ref>"
    assert embedding["secret_ref"] == "<redacted-ref>"
    assert host["bearer_secret_ref"] == "<redacted-ref>"
    assert "vault:" not in repr(diagnostic)
