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
def test_compose_has_no_removed_initialization_contract(name: str) -> None:
    source = (ROOT / name).read_text(encoding="utf-8")
    assert "openbiliclaw init" not in source
    assert "legacy" not in source.lower()
