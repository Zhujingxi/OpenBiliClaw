from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from openbiliclaw.api import dependencies as dependencies_module
from openbiliclaw.api.dependencies import build_application_container
from openbiliclaw.features.sources.domain import SourceId, SourceOperation, SourceTaskRequest
from openbiliclaw.features.sources.service import SourceTaskService
from openbiliclaw.features.system.domain import DatabaseSettings
from openbiliclaw.infrastructure.database.base import create_engine_and_session
from openbiliclaw.infrastructure.database.models import SettingModel
from openbiliclaw.infrastructure.database.operations import (
    SchemaNotReadyError,
    require_schema_at_head,
)
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs.source_composition import build_default_source_registry
from openbiliclaw.infrastructure.jobs.worker import database_runtime_factory

ROOT = Path(__file__).resolve().parents[2]


def _database_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _migrate(path: Path) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", _database_url(path))
    command.upgrade(config, "head")


def test_runtime_schema_check_rejects_unmigrated_database(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"
    database.touch()

    with pytest.raises(SchemaNotReadyError, match="db migrate"):
        require_schema_at_head(
            database_url=_database_url(database),
            alembic_ini=ROOT / "alembic.ini",
        )


def test_api_defers_source_settings_and_registry_until_after_schema_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "lazy-source-registry.db"
    _migrate(database)
    url = _database_url(database)
    engine = create_engine(url)
    with Session(engine) as session, session.begin():
        session.add(
            SettingModel(
                key="source-config:douyin",
                value={"mode": "extension"},
                updated_at=datetime.now(UTC),
            )
        )
    engine.dispose()
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", url)
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))
    calls = 0
    real_builder = dependencies_module.build_default_source_registry

    def observed_builder(session_factory, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real_builder(session_factory, **kwargs)

    monkeypatch.setattr(dependencies_module, "build_default_source_registry", observed_builder)

    container = build_application_container()
    assert calls == 0
    try:
        asyncio.run(container.startup())
        assert calls == 1
        assert container.sources.settings(SourceId.DOUYIN).settings["mode"] == "extension"
        assert (
            next(
                manifest
                for manifest in container.sources.manifests()
                if manifest.source_id is SourceId.DOUYIN
            )
            .operation_spec(SourceOperation.SEARCH)
            .transport_kind.value
            == "browser"
        )

        container.sources.update_settings(SourceId.DOUYIN, {"mode": "direct"})

        assert (
            next(
                manifest
                for manifest in container.sources.manifests()
                if manifest.source_id is SourceId.DOUYIN
            )
            .operation_spec(SourceOperation.SEARCH)
            .transport_kind.value
            == "direct"
        )
    finally:
        asyncio.run(container.shutdown())


def test_concurrent_source_setting_updates_serialize_registry_rebuilds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "serialized-source-refresh.db"
    _migrate(database)
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", _database_url(database))
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))
    real_builder = dependencies_module.build_default_source_registry
    state_lock = threading.Lock()
    active_builds = 0
    max_active_builds = 0

    def observed_builder(session_factory, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal active_builds, max_active_builds
        with state_lock:
            active_builds += 1
            max_active_builds = max(max_active_builds, active_builds)
        try:
            time.sleep(0.05)
            return real_builder(session_factory, **kwargs)
        finally:
            with state_lock:
                active_builds -= 1

    monkeypatch.setattr(dependencies_module, "build_default_source_registry", observed_builder)
    container = build_application_container()
    try:
        asyncio.run(container.startup())
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(
                    container.sources.update_settings,
                    SourceId.DOUYIN,
                    {"mode": "extension"},
                ),
                pool.submit(
                    container.sources.update_settings,
                    SourceId.REDDIT,
                    {},
                ),
            )
            for future in futures:
                future.result(timeout=2)

        assert max_active_builds == 1
        manifests = {manifest.source_id: manifest for manifest in container.sources.manifests()}
        assert (
            manifests[SourceId.DOUYIN].operation_spec(SourceOperation.SEARCH).transport_kind.value
            == "browser"
        )
        assert (
            manifests[SourceId.REDDIT].operation_spec(SourceOperation.SEARCH).transport_kind.value
            == "browser"
        )
    finally:
        asyncio.run(container.shutdown())


def test_independent_api_containers_read_the_same_persisted_source_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "multi-container-source-refresh.db"
    _migrate(database)
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", _database_url(database))
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))
    first = build_application_container()
    second = build_application_container()
    try:
        asyncio.run(first.startup())
        asyncio.run(second.startup())
        first.sources.update_settings(SourceId.DOUYIN, {"mode": "extension"})
        second.sources.update_settings(SourceId.REDDIT, {})

        for container in (first, second):
            manifests = {manifest.source_id: manifest for manifest in container.sources.manifests()}
            assert (
                manifests[SourceId.DOUYIN]
                .operation_spec(SourceOperation.SEARCH)
                .transport_kind.value
                == "browser"
            )
            assert (
                manifests[SourceId.REDDIT]
                .operation_spec(SourceOperation.SEARCH)
                .transport_kind.value
                == "browser"
            )
    finally:
        asyncio.run(first.shutdown())
        asyncio.run(second.shutdown())


def test_source_registry_preflight_failure_does_not_commit_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "atomic-source-refresh.db"
    _migrate(database)
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", _database_url(database))
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))
    real_builder = dependencies_module.build_default_source_registry
    reject_builds = False

    def controlled_builder(session_factory, **kwargs):  # type: ignore[no-untyped-def]
        if reject_builds:
            raise RuntimeError("registry preflight failed")
        return real_builder(session_factory, **kwargs)

    monkeypatch.setattr(dependencies_module, "build_default_source_registry", controlled_builder)
    container = build_application_container()
    try:
        asyncio.run(container.startup())
        reject_builds = True
        with pytest.raises(RuntimeError, match="preflight failed"):
            container.sources.update_settings(SourceId.DOUYIN, {"mode": "extension"})
        reject_builds = False
        assert container.sources.settings(SourceId.DOUYIN).settings["mode"] == "direct"
    finally:
        asyncio.run(container.shutdown())


def test_browser_row_enqueued_before_mode_switch_remains_claimable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "source-mode-switch-drain.db"
    _migrate(database)
    url = _database_url(database)
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", url)
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))
    container = build_application_container()
    engine = None
    try:
        asyncio.run(container.startup())
        container.sources.update_settings(SourceId.DOUYIN, {"mode": "extension"})
        engine, session_factory = create_engine_and_session(DatabaseSettings(url=url))
        extension_registry = build_default_source_registry(session_factory)
        worker_tasks = SourceTaskService(lambda: UnitOfWork(session_factory), extension_registry)
        task_id = worker_tasks.enqueue(
            SourceTaskRequest.model_validate(
                {
                    "source_id": "douyin",
                    "payload": {"operation": "search", "query": "python", "limit": 3},
                }
            )
        )

        container.sources.update_settings(SourceId.DOUYIN, {"mode": "direct"})

        claimed = container.source_tasks.claim("douyin")
        assert claimed is not None
        assert claimed.id == task_id
        assert claimed.operation is SourceOperation.SEARCH
    finally:
        if engine is not None:
            engine.dispose()
        asyncio.run(container.shutdown())


def test_api_schema_guard_failure_never_builds_the_source_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "unmigrated-api.db"
    database.touch()
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", _database_url(database))
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))
    calls = 0

    def unexpected_builder(_session_factory: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(dependencies_module, "build_default_source_registry", unexpected_builder)

    container = build_application_container()
    with pytest.raises(SchemaNotReadyError, match="db migrate"):
        asyncio.run(container.startup())
    assert calls == 0
    asyncio.run(container.shutdown())


def test_runtime_schema_check_is_read_only_and_concurrency_safe(tmp_path: Path) -> None:
    database = tmp_path / "migrated.db"
    _migrate(database)
    before = database.stat()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(
            executor.map(
                lambda _index: require_schema_at_head(
                    database_url=_database_url(database),
                    alembic_ini=ROOT / "alembic.ini",
                ),
                range(32),
            )
        )

    after = database.stat()
    assert results == (None,) * 32
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_size == before.st_size


def test_single_migration_owner_is_restart_safe(tmp_path: Path) -> None:
    database = tmp_path / "restart.db"

    _migrate(database)
    _migrate(database)

    require_schema_at_head(
        database_url=_database_url(database),
        alembic_ini=ROOT / "alembic.ini",
    )


def test_concurrent_api_worker_restart_never_runs_migrations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "restart-runtime.db"
    _migrate(database)
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", _database_url(database))
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))
    monkeypatch.setenv("OPENBILICLAW_LITELLM_API_KEY", "test-key")
    monkeypatch.setenv("OPENBILICLAW_HUEY_PATH", str(tmp_path / "huey.db"))

    def unexpected_upgrade(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("runtime process attempted to own migration")

    monkeypatch.setattr(command, "upgrade", unexpected_upgrade)
    container = build_application_container()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            api = executor.submit(lambda: asyncio.run(container.startup()))
            worker = executor.submit(database_runtime_factory)
            assert api.result() is None
            service, handlers = worker.result()
        assert service is not None
        assert set(handlers) == {
            "source_sync",
            "profile_projection",
            "feed_replenishment",
            "cleanup",
        }
    finally:
        asyncio.run(container.shutdown())


def test_concurrent_fresh_database_runtime_startup_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "fresh-runtime.db"
    database.touch()
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", _database_url(database))
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))
    monkeypatch.setenv("OPENBILICLAW_LITELLM_API_KEY", "test-key")
    container = build_application_container()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            api = executor.submit(lambda: asyncio.run(container.startup()))
            worker = executor.submit(database_runtime_factory)
            with pytest.raises(SchemaNotReadyError, match="db migrate"):
                api.result()
            with pytest.raises(SchemaNotReadyError, match="db migrate"):
                worker.result()
    finally:
        asyncio.run(container.shutdown())

    assert database.stat().st_size == 0


def test_api_startup_refuses_stale_schema_instead_of_migrating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "api-stale.db"
    database.touch()
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", _database_url(database))
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))

    container = build_application_container()
    with pytest.raises(SchemaNotReadyError, match="db migrate"):
        asyncio.run(container.startup())
    asyncio.run(container.shutdown())


def test_worker_startup_refuses_stale_schema_instead_of_migrating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "worker-stale.db"
    database.touch()
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", _database_url(database))
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))
    monkeypatch.setenv("OPENBILICLAW_LITELLM_API_KEY", "test-key")

    with pytest.raises(SchemaNotReadyError, match="db migrate"):
        database_runtime_factory()
