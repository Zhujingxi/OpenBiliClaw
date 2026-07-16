from __future__ import annotations

import json
from pathlib import Path

from openbiliclaw.api.app import create_app, generate_openapi
from openbiliclaw.features.activity.domain import ActivityEvent


def test_openapi_has_only_v1_paths_and_unique_explicit_operation_ids() -> None:
    schema = create_app().openapi()
    assert schema["paths"]
    assert all(path.startswith("/api/v1/") for path in schema["paths"])
    operation_ids = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert all(operation_id.startswith("v1_") for operation_id in operation_ids)


def test_frozen_metadata_is_a_string_keyed_json_object_schema() -> None:
    schema = ActivityEvent.model_json_schema()
    metadata = schema["properties"]["metadata"]
    assert metadata["type"] == "object"
    assert "additionalProperties" in metadata


def test_openapi_contains_no_secret_defaults_or_examples() -> None:
    payload = json.dumps(create_app().openapi(), sort_keys=True).casefold()
    assert "openbiliclaw_access_token" not in payload
    assert "openbiliclaw_secret_key" not in payload
    assert "litellm_master_key" not in payload
    assert "sk-" not in payload


def test_generated_openapi_is_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    generate_openapi(first)
    generate_openapi(second)
    assert first.read_bytes() == second.read_bytes()
    tracked = Path("openapi/openapi.json")
    assert tracked.read_bytes() == first.read_bytes()


def test_routers_do_not_import_infrastructure_or_construct_adapters() -> None:
    router_root = Path("src/openbiliclaw/api/routers")
    for path in router_root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "openbiliclaw.infrastructure" not in source, path
        assert "create_engine" not in source, path
        assert "AIHealthService(" not in source, path
        assert "Huey" not in source, path
