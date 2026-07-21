from __future__ import annotations

import json
from pathlib import Path

from openbiliclaw.api.app import create_app, generate_openapi
from openbiliclaw.features.activity.domain import ActivityEvent

PROTECTED_OPERATION_IDS = {
    "v1_auth_logout",
    "v1_auth_revoke",
    "v1_system_ai_health",
    "v1_settings_get",
    "v1_settings_patch",
    "v1_sources_list",
    "v1_sources_status",
    "v1_sources_get_settings",
    "v1_sources_update_settings",
    "v1_sources_configure_account",
    "v1_sources_disconnect_account",
    "v1_source_tasks_claim",
    "v1_source_tasks_complete",
    "v1_events_ingest",
    "v1_profile_get",
    "v1_feed_list",
    "v1_interactions_create",
    "v1_library_list",
    "v1_library_add",
    "v1_library_remove",
    "v1_chat_stream",
    "v1_jobs_schedule",
    "v1_jobs_list",
    "v1_jobs_get",
    "v1_jobs_cancel",
    "v1_jobs_events",
}

PUBLIC_OPERATION_IDS = {
    "v1_auth_extension_token",
    "v1_auth_login",
    "v1_auth_status",
    "v1_system_readiness",
}

ONBOARDING_OPERATION_IDS = {
    "v1_onboarding_get",
    "v1_onboarding_start",
    "v1_onboarding_events",
}


def _operations(schema: dict[str, object]):
    paths = schema["paths"]
    assert isinstance(paths, dict)
    for path_item in paths.values():
        assert isinstance(path_item, dict)
        for method, operation in path_item.items():
            if method in {"get", "post", "put", "patch", "delete"}:
                assert isinstance(operation, dict)
                yield operation


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


def test_openapi_advertises_session_security_and_public_exceptions() -> None:
    schema = create_app().openapi()
    assert schema["components"]["securitySchemes"] == {
        "BearerAuth": {"type": "http", "scheme": "bearer"},
        "SessionCookie": {"type": "apiKey", "in": "cookie", "name": "obc_session"},
    }
    by_id = {operation["operationId"]: operation for operation in _operations(schema)}
    for operation_id in PROTECTED_OPERATION_IDS:
        assert by_id[operation_id]["security"] == [
            {"BearerAuth": []},
            {"SessionCookie": []},
        ], operation_id
    for operation_id in PUBLIC_OPERATION_IDS:
        assert "security" not in by_id[operation_id], operation_id
    for operation_id in ONBOARDING_OPERATION_IDS:
        assert by_id[operation_id]["security"] == [
            {},
            {"BearerAuth": []},
            {"SessionCookie": []},
        ], operation_id


def test_retained_json_success_responses_have_concrete_dtos() -> None:
    schema = create_app().openapi()
    by_id = {operation["operationId"]: operation for operation in _operations(schema)}
    expected = {
        "v1_sources_list": {
            "type": "array",
            "items": {"$ref": "#/components/schemas/SourceManifest"},
        },
        "v1_sources_status": {
            "type": "array",
            "items": {"$ref": "#/components/schemas/SourceAccountStatus"},
        },
        "v1_sources_get_settings": {"$ref": "#/components/schemas/SourceSettingsState"},
        "v1_sources_update_settings": {"$ref": "#/components/schemas/SourceSettingsState"},
        "v1_events_ingest": {"$ref": "#/components/schemas/EventIngestResponse"},
        "v1_feed_list": {"type": "array", "items": {"$ref": "#/components/schemas/FeedItem"}},
        "v1_interactions_create": {"$ref": "#/components/schemas/InteractionResponse"},
        "v1_library_list": {
            "type": "array",
            "items": {"$ref": "#/components/schemas/LibraryItem"},
        },
        "v1_library_add": {"$ref": "#/components/schemas/CollectionItem"},
    }
    for operation_id, contract in expected.items():
        operation = by_id[operation_id]
        success = next(
            response
            for code, response in operation["responses"].items()
            if str(code).startswith("2") and response.get("content")
        )
        response_schema = success["content"]["application/json"]["schema"]
        for key, value in contract.items():
            assert response_schema[key] == value, operation_id
        assert response_schema not in ({}, {"type": "object"}), operation_id
        assert set(response_schema) != {"title"}, operation_id


def test_sse_operations_document_typed_event_stream_contracts() -> None:
    schema = create_app().openapi()
    by_id = {operation["operationId"]: operation for operation in _operations(schema)}
    expected_events = {
        "v1_chat_stream": {"delta", "done", "error"},
        "v1_jobs_events": {"progress", "done", "error"},
        "v1_onboarding_events": {"progress", "done", "error"},
    }
    for operation_id, event_names in expected_events.items():
        response = by_id[operation_id]["responses"]["200"]
        assert set(response["content"]) == {"text/event-stream"}, operation_id
        media = response["content"]["text/event-stream"]
        assert media["schema"] == {"type": "string"}
        assert set(media["x-sse-events"]) == event_names
        for event in media["x-sse-events"].values():
            ref = event["schema"]["$ref"]
            assert ref.startswith("#/components/schemas/")
            assert ref.rsplit("/", 1)[-1] in schema["components"]["schemas"]


def test_onboarding_openapi_requires_sources_and_tracks_durable_child_runs() -> None:
    schema = create_app().openapi()
    by_id = {operation["operationId"]: operation for operation in _operations(schema)}
    start_schema = schema["components"]["schemas"]["OnboardingStart"]
    events = by_id["v1_onboarding_events"]["responses"]["200"]["content"]["text/event-stream"][
        "x-sse-events"
    ]

    assert start_schema["required"] == ["source_ids"]
    assert start_schema["properties"]["source_ids"]["minItems"] == 1
    assert events["progress"]["schema"] == {"$ref": "#/components/schemas/OnboardingProgressEvent"}
    assert events["done"]["schema"] == {"$ref": "#/components/schemas/OnboardingTerminalEvent"}


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
