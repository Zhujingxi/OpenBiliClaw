from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, load_config, save_config

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-test", model="gpt-4o-mini"),
        )
    )
    config_path = tmp_path / "config.toml"
    save_config(config, config_path)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app)


def test_config_api_exposes_and_updates_saved_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _make_client(monkeypatch, tmp_path)

    assert client.get("/api/config").json()["saved_sync"] == {
        "auto_sync_enabled": False
    }

    response = client.put(
        "/api/config",
        json={"saved_sync": {"auto_sync_enabled": True}},
    )

    assert response.status_code == 200
    assert client.get("/api/config").json()["saved_sync"] == {
        "auto_sync_enabled": True
    }
    assert load_config(tmp_path / "config.toml").saved_sync.auto_sync_enabled is True


def test_config_api_rejects_non_boolean_saved_auto_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _make_client(monkeypatch, tmp_path)

    response = client.put(
        "/api/config",
        json={"saved_sync": {"auto_sync_enabled": "true"}},
    )

    assert response.status_code == 422
    assert load_config(tmp_path / "config.toml").saved_sync.auto_sync_enabled is False
