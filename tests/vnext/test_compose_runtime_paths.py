from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.prebuilt.yml"])
def test_api_and_worker_share_exact_database_queue_and_mount(name: str) -> None:
    compose = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    api = compose["services"]["api"]
    worker = compose["services"]["worker"]

    for key in ("OPENBILICLAW_DATABASE_URL", "OPENBILICLAW_HUEY_PATH"):
        assert api["environment"][key] == worker["environment"][key]
    assert api["environment"]["OPENBILICLAW_DATABASE_URL"] == (
        "sqlite:////app/runtime/data/vnext/openbiliclaw.db"
    )
    assert api["environment"]["OPENBILICLAW_HUEY_PATH"] == ("/app/runtime/data/vnext/huey.db")
    assert "openbiliclaw_data:/app/runtime/data" in api["volumes"]
    assert "openbiliclaw_data:/app/runtime/data" in worker["volumes"]


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.prebuilt.yml"])
def test_compose_serializes_migration_before_api_and_worker(name: str) -> None:
    compose = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    services = compose["services"]

    assert set(services) == {"migrate", "api", "worker", "litellm", "litellm-postgres"}
    migrate = services["migrate"]
    assert migrate["command"] == ["openbiliclaw", "db", "migrate"]
    assert (
        migrate["environment"]["OPENBILICLAW_DATABASE_URL"]
        == (services["api"]["environment"]["OPENBILICLAW_DATABASE_URL"])
    )
    assert "openbiliclaw_data:/app/runtime/data" in migrate["volumes"]
    assert migrate["restart"] == "no"
    for service_name in ("api", "worker"):
        dependencies = services[service_name]["depends_on"]
        assert dependencies["migrate"]["condition"] == "service_completed_successfully"
        assert dependencies["litellm"]["condition"] == "service_healthy"
        assert "required" not in dependencies["migrate"]


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.prebuilt.yml"])
def test_worker_healthcheck_validates_runtime_dependencies(name: str) -> None:
    compose = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    healthcheck = compose["services"]["worker"]["healthcheck"]

    assert healthcheck["test"] == [
        "CMD",
        "python",
        "-m",
        "openbiliclaw.infrastructure.jobs.health",
    ]
    assert healthcheck["interval"] == "30s"
    assert healthcheck["timeout"] == "5s"
    assert healthcheck["start_period"] == "20s"
    assert healthcheck["retries"] == 3


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.prebuilt.yml"])
def test_compose_contains_no_retired_local_provider_stack(name: str) -> None:
    source = (ROOT / name).read_text(encoding="utf-8").lower()

    for retired in ("ollama", "bge-m3", "seed_ollama", "embedding_model"):
        assert retired not in source


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.prebuilt.yml"])
def test_compose_has_no_removed_initialization_contract(name: str) -> None:
    source = (ROOT / name).read_text(encoding="utf-8")
    assert "openbiliclaw init" not in source
    assert "legacy" not in source.lower()
