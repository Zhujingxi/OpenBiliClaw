"""Persisted, source-owned settings behind self-describing manifests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import select

from openbiliclaw.api.app import create_app
from openbiliclaw.features.sources.domain import SourceId, SourceOperation, SourceSettingsState
from openbiliclaw.features.sources.service import SourceAccountService
from openbiliclaw.features.system.service import SettingsService
from openbiliclaw.infrastructure.database.base import DatabaseSettings, create_engine_and_session
from openbiliclaw.infrastructure.database.models import SettingModel
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs.source_composition import build_default_source_registry

from .test_connector_contract import RecordingTransport, make_registry

if TYPE_CHECKING:
    from pathlib import Path


SOURCE_IDS = tuple(source_id.value for source_id in SourceId)
VALID_PATCHES: dict[SourceId, dict[str, object]] = {
    SourceId.BILIBILI: {},
    SourceId.XIAOHONGSHU: {},
    SourceId.DOUYIN: {"mode": "extension"},
    SourceId.YOUTUBE: {},
    SourceId.TWITTER: {},
    SourceId.ZHIHU: {},
    SourceId.REDDIT: {"backend": "extension"},
}


@pytest.fixture
def settings_context(tmp_path: Path) -> tuple[Any, Any, SourceAccountService]:
    url = f"sqlite:///{tmp_path / 'source-settings.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=url))
    registry = make_registry({source_id: RecordingTransport(source_id) for source_id in SOURCE_IDS})
    service = SourceAccountService(
        lambda: UnitOfWork(session_factory),
        cipher=object(),  # type: ignore[arg-type]
        registry=registry,
    )
    yield session_factory, registry, service
    engine.dispose()


def test_all_seven_source_settings_round_trip_through_their_package_models(
    settings_context: tuple[Any, Any, SourceAccountService],
) -> None:
    session_factory, registry, service = settings_context

    for source_id in SourceId:
        initial = service.settings(source_id)
        assert initial.source_id is source_id
        assert dict(initial.settings) == registry.get(source_id.value).settings.model_dump()

        updated = service.update_settings(source_id, VALID_PATCHES[source_id])
        assert updated.source_id is source_id
        for key, value in VALID_PATCHES[source_id].items():
            assert updated.settings[key] == value
        assert service.settings(source_id) == updated

    with session_factory() as session:
        rows = session.scalars(select(SettingModel)).all()
    assert len(rows) == 7
    serialized = json.dumps({row.key: row.value for row in rows}, sort_keys=True)
    assert all(row.key.startswith("source-config:") for row in rows)
    assert not any(
        token in serialized.casefold()
        for token in ('"credentials"', '"access_token"', '"password"')
    )


def test_invalid_source_patch_rolls_back_atomically_and_does_not_affect_disconnect(
    settings_context: tuple[Any, Any, SourceAccountService],
) -> None:
    _, _, service = settings_context
    valid = service.update_settings(SourceId.DOUYIN, {"mode": "extension"})

    with pytest.raises(ValidationError):
        service.update_settings(
            SourceId.DOUYIN,
            {"mode": "unsupported", "cookie": "must-never-persist"},
        )

    assert service.settings(SourceId.DOUYIN) == valid
    disconnected = service.disconnect(SourceId.DOUYIN, "not-configured")
    assert disconnected == service.disconnect(SourceId.DOUYIN, "not-configured")
    assert disconnected.idempotent is True


def test_global_settings_replace_preserves_source_configuration_rows(
    settings_context: tuple[Any, Any, SourceAccountService],
) -> None:
    session_factory, _, service = settings_context
    expected = service.update_settings(SourceId.REDDIT, {"backend": "extension"})

    global_settings = SettingsService(lambda: UnitOfWork(session_factory))
    assert global_settings.update({"feed": {"low_watermark": 7}}).feed.low_watermark == 7
    assert service.settings(SourceId.REDDIT) == expected


def test_restart_composition_consumes_persisted_transport_setting(
    settings_context: tuple[Any, Any, SourceAccountService],
) -> None:
    session_factory, _, service = settings_context
    state = service.update_settings(
        SourceId.DOUYIN,
        {"mode": "extension"},
    )
    assert state == SourceSettingsState(
        source_id=SourceId.DOUYIN,
        settings=state.settings,
    )

    restarted = build_default_source_registry(session_factory)
    connector = restarted.get("douyin")
    assert connector.settings.mode == "extension"
    assert (
        connector.manifest.operation_spec(SourceOperation.SEARCH).transport_kind.value == "browser"
    )


def test_every_advertised_source_setting_has_a_named_runtime_consumer(
    settings_context: tuple[Any, Any, SourceAccountService],
) -> None:
    _, _, service = settings_context
    expected_fields = {
        SourceId.BILIBILI: set(),
        SourceId.XIAOHONGSHU: set(),
        SourceId.DOUYIN: {"mode"},
        SourceId.YOUTUBE: set(),
        SourceId.TWITTER: set(),
        SourceId.ZHIHU: set(),
        SourceId.REDDIT: {"backend"},
    }

    for manifest in service.manifests():
        properties = manifest.settings_schema["properties"]
        assert set(properties) == expected_fields[manifest.source_id]
        assert all(property_schema.get("x-consumer") for property_schema in properties.values())


def test_source_settings_state_and_manifest_are_credential_free(
    settings_context: tuple[Any, Any, SourceAccountService],
) -> None:
    _, _, service = settings_context
    state = service.update_settings(SourceId.REDDIT, {"backend": "extension"})
    payload = state.model_dump_json().casefold()
    assert "cookie" not in payload
    assert "credential" not in payload
    for manifest in service.manifests():
        dumped = manifest.model_dump(mode="json")
        schema = json.dumps(dumped["settings_schema"], sort_keys=True).casefold()
        assert not any(
            token in schema for token in ('"credentials"', '"access_token"', '"password"')
        )

    openapi = create_app().openapi()
    settings_contracts = json.dumps(
        {
            name: schema
            for name, schema in openapi["components"]["schemas"].items()
            if name in {"SourceSettingsState", "SourceSettingsUpdate"}
        },
        sort_keys=True,
    ).casefold()
    assert "credential" not in settings_contracts
    assert "password" not in settings_contracts
