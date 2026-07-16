"""Characterization tests for the fresh vNext persistence boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from inspect import signature
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind
from openbiliclaw.features.feed.domain import ContentItem
from openbiliclaw.features.profile.domain import ProfileFacet, ProfileSnapshot
from openbiliclaw.infrastructure.database.base import (
    DatabaseSettings,
    create_engine_and_session,
)
from openbiliclaw.infrastructure.database.models import AIRunModel, SourceTaskModel
from openbiliclaw.infrastructure.database.repositories import ProfileRevisionConflict
from openbiliclaw.infrastructure.database.uow import UnitOfWork

CONTENT_ID = UUID("00000000-0000-0000-0000-000000000101")
PROFILE_ID = UUID("00000000-0000-0000-0000-000000000102")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000104")
NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
REPOSITORY_ROOT = Path(__file__).parents[2]

EXPECTED_TABLES = {
    "settings",
    "source_accounts",
    "activity_events",
    "profile_revisions",
    "profile_evidence",
    "profile_consumed_evidence",
    "content_items",
    "candidate_assessments",
    "feed_entries",
    "interactions",
    "collections",
    "collection_items",
    "chat_turns",
    "source_tasks",
    "job_runs",
    "ai_runs",
}


def _url(path: Path) -> str:
    return f"sqlite:///{path}"


def _migrate(path: Path, revision: str = "head") -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", _url(path))
    command.upgrade(config, revision)


@pytest.fixture
def migrated_database(tmp_path: Path) -> Path:
    path = tmp_path / "vnext.db"
    _migrate(path)
    return path


def test_fresh_migration_creates_only_vnext_schema_and_predefined_collections(
    migrated_database: Path,
) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=_url(migrated_database))
    )

    assert set(inspect(engine).get_table_names()) == {"alembic_version", *EXPECTED_TABLES}
    ai_run_columns = {column["name"] for column in inspect(engine).get_columns("ai_runs")}
    assert "input_payload" not in ai_run_columns
    assert "output_payload" not in ai_run_columns
    source_task_columns = {column["name"] for column in inspect(engine).get_columns("source_tasks")}
    assert "request_deadline_at" in source_task_columns
    assert source_task_columns == set(SourceTaskModel.__table__.columns.keys())
    source_task_indexes = {
        index["name"]: index["column_names"]
        for index in inspect(engine).get_indexes("source_tasks")
    }
    assert source_task_indexes["source_task_claim"] == [
        "source_id",
        "status",
        "request_deadline_at",
        "created_at",
    ]
    with UnitOfWork(session_factory) as uow:
        collections = uow.collections.list_predefined()
    assert [(collection.slug, collection.display_name) for collection in collections] == [
        ("favorites", "Favorites"),
        ("watch_later", "Watch later"),
    ]

    engine.dispose()


def test_migration_supports_downgrade_then_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "cycle.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", _url(path))

    command.upgrade(config, "head")
    engine, _ = create_engine_and_session(DatabaseSettings(url=_url(path)))
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES
    engine.dispose()

    command.downgrade(config, "base")
    engine, _ = create_engine_and_session(DatabaseSettings(url=_url(path)))
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()

    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=_url(path)))
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES
    with UnitOfWork(session_factory) as uow:
        assert len(uow.collections.list_predefined()) == 2
    engine.dispose()


def test_default_migration_creates_vnext_parent_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))

    command.upgrade(config, "head")

    assert (tmp_path / "data" / "vnext" / "openbiliclaw.db").is_file()


def test_unit_of_work_rolls_back_uncommitted_transaction(migrated_database: Path) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=_url(migrated_database))
    )
    item = ContentItem(
        id=CONTENT_ID,
        source_id="bilibili",
        external_id="BV1rollback",
        url="https://www.bilibili.com/video/BV1rollback",
        title="Rollback",
    )

    with UnitOfWork(session_factory) as uow:
        uow.content.add(item)

    with UnitOfWork(session_factory) as uow:
        assert uow.content.get_by_identity("bilibili", "BV1rollback") is None
    engine.dispose()


def test_content_identity_is_unique_per_source(migrated_database: Path) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=_url(migrated_database))
    )
    original = ContentItem(
        id=CONTENT_ID,
        source_id="bilibili",
        external_id="BV1same",
        url="https://www.bilibili.com/video/BV1same",
        title="Original",
    )
    duplicate = original.model_copy(
        update={"id": UUID("00000000-0000-0000-0000-000000000103"), "title": "Duplicate"}
    )

    with UnitOfWork(session_factory) as uow:
        uow.content.add(original)
        uow.commit()
    with pytest.raises(IntegrityError), UnitOfWork(session_factory) as uow:
        uow.content.add(duplicate)
        uow.commit()

    with UnitOfWork(session_factory) as uow:
        stored = uow.content.get_by_identity("bilibili", "BV1same")
    assert stored == original
    engine.dispose()


def test_profile_append_rejects_stale_expected_revision(migrated_database: Path) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=_url(migrated_database))
    )
    initial = ProfileSnapshot(id=PROFILE_ID, revision=0, narrative="Initial", created_at=NOW)
    next_snapshot = initial.model_copy(update={"revision": 1, "narrative": "Current"})
    stale_snapshot = initial.model_copy(update={"revision": 1, "narrative": "Stale"})

    with UnitOfWork(session_factory) as uow:
        uow.profiles.append(initial, expected_revision=None)
        uow.commit()
    with UnitOfWork(session_factory) as uow:
        observed = uow.profiles.latest()
        assert observed == initial
    with UnitOfWork(session_factory) as uow:
        uow.profiles.append(next_snapshot, expected_revision=observed.revision)
        uow.commit()
    with (
        pytest.raises(ProfileRevisionConflict, match="expected revision 0, found 1"),
        UnitOfWork(session_factory) as uow,
    ):
        uow.profiles.append(stale_snapshot, expected_revision=observed.revision)

    with UnitOfWork(session_factory) as uow:
        assert uow.profiles.latest() == next_snapshot
    engine.dispose()


def test_concurrent_profile_writers_raise_domain_conflict(migrated_database: Path) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=_url(migrated_database))
    )
    initial = ProfileSnapshot(id=PROFILE_ID, revision=0, narrative="Initial", created_at=NOW)
    first_update = initial.model_copy(update={"revision": 1, "narrative": "First"})
    second_update = initial.model_copy(update={"revision": 1, "narrative": "Second"})
    with UnitOfWork(session_factory) as uow:
        uow.profiles.append(initial, expected_revision=None)
        uow.commit()

    with UnitOfWork(session_factory) as first, UnitOfWork(session_factory) as second:
        assert first.profiles.latest() == initial
        assert second.profiles.latest() == initial
        first.profiles.append(first_update, expected_revision=0)
        with pytest.raises(ProfileRevisionConflict, match="written concurrently"):
            second.profiles.append(second_update, expected_revision=0)
        first.commit()

    with UnitOfWork(session_factory) as uow:
        assert uow.profiles.latest() == first_update

    engine.dispose()


def test_ai_run_repository_has_no_input_or_output_payload_persistence_channel(
    migrated_database: Path,
) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=_url(migrated_database))
    )
    with UnitOfWork(session_factory) as uow:
        assert "input_payload" not in signature(uow.ai_runs.add_started).parameters
        assert "input_payload" not in signature(uow.ai_runs.succeed).parameters
        assert "output_payload" not in signature(uow.ai_runs.succeed).parameters
        run_id = uow.ai_runs.add_started(task_name="profile_delta", model_alias="obc-analysis")
        uow.commit()

    with session_factory() as session:
        row = session.get(AIRunModel, str(run_id))
        assert row is not None
        assert row.task_name == "profile_delta"
        assert row.model_alias == "obc-analysis"
    engine.dispose()


def test_profile_facets_round_trip_with_persisted_activity_evidence(
    migrated_database: Path,
) -> None:
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=_url(migrated_database))
    )
    event = ActivityEvent(
        id=EVENT_ID,
        source_id="bilibili",
        kind=ActivityKind.FAVORITE,
        occurred_at=NOW,
        content_external_id="BV1evidence",
        metadata={"folder": "architecture"},
    )
    facet = ProfileFacet(
        name="interests",
        value="Architecture",
        weight=0.8,
        confidence=0.9,
        evidence_ids=(EVENT_ID, EVENT_ID),
    )
    profile = ProfileSnapshot(
        id=PROFILE_ID,
        revision=0,
        facets=(facet,),
        confidence=0.9,
        created_at=NOW,
    )

    with UnitOfWork(session_factory) as uow:
        uow.activities.add(event)
        uow.profiles.append(profile, expected_revision=None)
        uow.commit()

    with UnitOfWork(session_factory) as uow:
        assert uow.profiles.latest() == profile
    engine.dispose()
