from __future__ import annotations

import json
from pathlib import Path
from typing import Never

import httpx

from openbiliclaw.ai.providers.catalog import ModelCatalog
from openbiliclaw.core.config import AppSettings, EmbeddingSettings, ModelSettings
from openbiliclaw.hosts.api import HostDependencies, create_app
from openbiliclaw.hosts.api.model_configuration import FileModelConfiguration
from openbiliclaw.infrastructure.credentials.keyring import ProtectedFileBackend
from openbiliclaw.infrastructure.credentials.vault import CredentialVault

_FIXTURE = Path("tests/fixtures/models.dev.small.json")
_HEADERS = {"X-Device-ID": "device", "X-CSRF-Token": "device"}


class Facade:
    def __getattr__(self, name: str) -> Never:
        raise AssertionError(f"unexpected facade access: {name}")


def _dependencies(tmp_path: Path, settings: AppSettings | None = None) -> HostDependencies:
    cache = tmp_path / "models.dev.json"
    cache.write_bytes(_FIXTURE.read_bytes())
    config = tmp_path / "config.toml"
    config.write_text(
        '[model]\nprovider = "openai"\nmodel_name = ""\n\n[embedding]\nmodel_name = ""\n',
        encoding="utf-8",
    )
    selected = settings or AppSettings()
    return HostDependencies(
        facade=Facade(),
        models=FileModelConfiguration(
            settings=selected,
            config_path=config,
            catalog=ModelCatalog(cache, fetch=lambda _: _FIXTURE.read_bytes()),
            vault=CredentialVault(ProtectedFileBackend(tmp_path / "credentials.json")),
        ),
    )


async def test_catalog_projects_only_ui_safe_metadata(tmp_path: Path) -> None:
    app = create_app(_dependencies(tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/models/catalog")
    assert response.status_code == 200
    body = response.json()
    providers = {provider["id"]: provider for provider in body["providers"]}
    assert providers["deepseek"]["protocol"] == "openai"
    assert providers["kimi-for-coding"]["protocol"] == "anthropic"
    assert set(providers["deepseek"]) == {"id", "name", "env", "protocol", "models"}


async def test_catalog_omits_protocols_the_runtime_cannot_configure(tmp_path: Path) -> None:
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    fixture["unsupported"] = {
        "id": "unsupported",
        "npm": "@vendor/unknown",
        "models": {},
    }
    dependencies = _dependencies(tmp_path)
    cache = tmp_path / "models.dev.json"
    cache.write_text(json.dumps(fixture), encoding="utf-8")
    app = create_app(dependencies)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/models/catalog")
    assert response.status_code == 200
    assert "unsupported" not in {item["id"] for item in response.json()["providers"]}


async def test_current_reports_presence_never_secret_references(tmp_path: Path) -> None:
    settings = AppSettings(
        model=ModelSettings(
            provider="deepseek",
            model_name="deepseek-chat",
            secret_ref="vault:cred_11111111111111111111111111111111",
        ),
        embedding=EmbeddingSettings(
            model_name="embedding-model",
            secret_ref="vault:cred_22222222222222222222222222222222",
        ),
    )
    app = create_app(_dependencies(tmp_path, settings))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/models/current")
    assert response.status_code == 200
    body = response.json()
    serialized = json.dumps(body)
    assert "cred_" not in serialized
    assert body["current"]["model"]["secret_configured"] is True
    assert body["current"]["model"]["protocol"] == "openai"
    assert body["current"]["embedding"]["secret_configured"] is True


async def test_update_validates_persists_secret_reference_and_requires_restart(
    tmp_path: Path,
) -> None:
    dependencies = _dependencies(tmp_path)
    app = create_app(dependencies)
    key = "unit-test-key-material"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        invalid = await client.put(
            "/v1/models/current",
            json={"provider": "deepseek", "model_name": "absent", "api_key": key},
            headers=_HEADERS,
        )
        saved = await client.put(
            "/v1/models/current",
            json={
                "provider": "deepseek",
                "model_name": "deepseek-chat",
                "api_key": key,
            },
            headers=_HEADERS,
        )
        current = await client.get("/v1/models/current")
    assert invalid.status_code == 422
    assert saved.status_code == 200
    assert saved.json()["restart_required"] is True
    assert saved.json()["reloaded"] is False
    assert current.json()["current"]["model"]["secret_configured"] is True
    config = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert key not in config
    assert 'provider = "deepseek"' in config
    assert "vault:cred_" in config


async def test_custom_provider_requires_complete_explicit_contract(tmp_path: Path) -> None:
    app = create_app(_dependencies(tmp_path))
    base = {"provider": "private", "model_name": "chat"}
    capabilities = {
        "tools": True,
        "structured_output": False,
        "vision": False,
        "context_tokens": 4096,
        "streaming": True,
        "reasoning": False,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        incomplete = await client.put("/v1/models/current", json=base, headers=_HEADERS)
        complete = await client.put(
            "/v1/models/current",
            json={
                **base,
                "protocol": "openai",
                "endpoint": "https://private.example/v1",
                "capabilities": capabilities,
            },
            headers=_HEADERS,
        )
    assert incomplete.status_code == 422
    assert complete.status_code == 200
    assert complete.json()["current"]["model"]["capabilities"]["context_tokens"] == 4096
