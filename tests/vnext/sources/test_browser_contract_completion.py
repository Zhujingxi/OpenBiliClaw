"""Self-describing source forms and typed browser-operation contracts."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from openbiliclaw.api.routers.sources import disconnect_source
from openbiliclaw.features.sources.domain import (
    BrowserOperationResult,
    BrowserSearchRequest,
    SourceAccountDisconnectResult,
    SourceId,
    SourceOperation,
    SourceTaskRequest,
)
from openbiliclaw.features.sources.service import SourceAccountService

from .test_connector_contract import RecordingTransport, make_registry

SOURCE_IDS = (
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "zhihu",
    "reddit",
)


@pytest.fixture
def registry():  # type: ignore[no-untyped-def]
    return make_registry({source_id: RecordingTransport(source_id) for source_id in SOURCE_IDS})


def _assert_schema_is_secret_safe(schema: dict[str, Any]) -> None:
    serialized = str(schema).casefold()
    assert "example" not in serialized
    assert "default': '" not in serialized
    for definition in schema.get("$defs", {}).values():
        if definition.get("properties"):
            _assert_schema_is_secret_safe(definition)


def test_all_manifests_expose_pydantic_derived_safe_form_and_operation_schemas(
    registry: Any,
) -> None:
    for manifest in registry.manifests.values():
        assert manifest.settings_schema["type"] == "object"
        if manifest.source_id is SourceId.REDDIT:
            assert dict(manifest.credential_schema) == {}
        else:
            assert manifest.credential_schema["type"] == "object"
            credential_properties = manifest.credential_schema["properties"]
            assert credential_properties
            assert all(field.get("writeOnly") is True for field in credential_properties.values())
            _assert_schema_is_secret_safe(manifest.credential_schema)
        for operation in manifest.operations:
            assert operation.request_schema["type"] == "object"
            assert operation.result_schema["type"] == "object"
            assert operation.request_schema["properties"]["operation"]["const"] == (
                operation.operation.value
            )
            assert operation.result_schema["properties"]["operation"]["const"] == (
                operation.operation.value
            )


def test_browser_task_request_is_a_strict_discriminated_operation_union() -> None:
    request = SourceTaskRequest(
        source_id="zhihu",
        payload={"operation": "search", "query": " python ", "limit": 5},
    )
    assert isinstance(request.payload, BrowserSearchRequest)
    assert request.operation is SourceOperation.SEARCH
    assert request.payload.query == "python"

    with pytest.raises(ValidationError):
        SourceTaskRequest.model_validate(
            {
                "source_id": "zhihu",
                "operation": "search",
                "payload": {"query": "python", "limit": 5},
            }
        )
    with pytest.raises(ValidationError):
        SourceTaskRequest.model_validate(
            {"source_id": "zhihu", "payload": {"query": "python", "limit": 5}}
        )

    for malformed in (
        {"operation": "search", "limit": 5},
        {"operation": "trending", "query": "not-allowed", "limit": 5},
        {"operation": "related", "seed": "", "limit": 5},
        {"operation": "native_save", "limit": 5},
        {"operation": "feed", "limit": float("nan")},
        {"operation": "feed", "limit": 5, "cookie": "never"},
    ):
        with pytest.raises((ValidationError, RuntimeError)):
            SourceTaskRequest(source_id="zhihu", payload=malformed)


def test_browser_completion_envelope_is_discriminated_typed_and_secret_safe() -> None:
    result = BrowserOperationResult.validate_python(
        {"operation": "search", "items": [{"content_id": "1", "score": 0.5}]}
    )
    assert result.operation is SourceOperation.SEARCH
    assert result.items[0]["content_id"] == "1"

    for malformed in (
        {"items": []},
        {"operation": "search", "items": [], "extra": True},
        {"operation": "search", "items": [{"score": float("inf")}]},
        {"operation": "search", "items": [{"access_token": "never"}]},
    ):
        with pytest.raises((ValidationError, ValueError, RuntimeError)):
            BrowserOperationResult.validate_python(malformed)


class _AccountRepository:
    def __init__(self) -> None:
        self.rows = {("zhihu", "primary")}

    def delete(self, *, source_id: str, account_key: str) -> bool:
        identity = (source_id, account_key)
        existed = identity in self.rows
        self.rows.discard(identity)
        return existed


class _AccountUow:
    def __init__(self, repository: _AccountRepository) -> None:
        self.source_accounts = repository
        self.commits = 0

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1


def test_account_disconnect_is_typed_and_idempotent_without_credential_echo(registry: Any) -> None:
    repository = _AccountRepository()
    uow = _AccountUow(repository)
    service = SourceAccountService(lambda: uow, cipher=object(), registry=registry)  # type: ignore[arg-type]

    first = service.disconnect(SourceId.ZHIHU, " primary ")
    second = service.disconnect(SourceId.ZHIHU, "primary")

    assert first == SourceAccountDisconnectResult(
        source_id=SourceId.ZHIHU,
        account_key="primary",
        disconnected=True,
        idempotent=False,
    )
    assert second.idempotent is True
    assert "credential" not in first.model_dump_json().casefold()
    assert uow.commits == 2


def test_disconnect_router_returns_the_typed_idempotent_result(registry: Any) -> None:
    class Sources:
        def disconnect(
            self, source_id: SourceId, account_key: str
        ) -> SourceAccountDisconnectResult:
            return SourceAccountDisconnectResult(
                source_id=source_id,
                account_key=account_key,
                disconnected=True,
                idempotent=True,
            )

    container = type("Container", (), {"sources": Sources()})()
    result = disconnect_source(SourceId.REDDIT, "primary", container)  # type: ignore[arg-type]
    assert result.source_id is SourceId.REDDIT
    assert result.idempotent is True
