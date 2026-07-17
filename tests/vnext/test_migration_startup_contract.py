from __future__ import annotations

import asyncio
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
from openbiliclaw.features.sources.domain import SourceId
from openbiliclaw.infrastructure.database.models import SettingModel
from openbiliclaw.infrastructure.database.operations import (
    SchemaNotReadyError,
    require_schema_at_head,
)
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

    def observed_builder(session_factory):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real_builder(session_factory)

    monkeypatch.setattr(dependencies_module, "build_default_source_registry", observed_builder)

    container = build_application_container()
    assert calls == 0
    try:
        asyncio.run(container.startup())
        assert calls == 1
        assert container.sources.settings(SourceId.DOUYIN).settings["mode"] == "extension"
    finally:
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
