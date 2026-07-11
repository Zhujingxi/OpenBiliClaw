from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.saved_sync.models import SavedItemInput
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "saved-sync.db")
    database.initialize()
    return database


def test_saved_memberships_allow_same_raw_id_on_two_platforms(db: Database) -> None:
    x = SavedItemInput(source_platform="twitter", content_id="123", title="x")
    dy = SavedItemInput(source_platform="douyin", content_id="123", title="dy")

    db.upsert_saved_membership("favorite", x)
    db.upsert_saved_membership("favorite", dy)

    rows = db.list_saved_memberships("favorite")
    assert {row["item_key"] for row in rows} == {"twitter:123", "douyin:123"}
    assert {row["sync_status"] for row in rows} == {"pending"}


def test_saved_membership_upsert_refreshes_item_snapshot_and_note(db: Database) -> None:
    item = SavedItemInput(source_platform="x", content_id="123", title="old")
    db.upsert_saved_membership("favorite", item, note="first")

    updated = SavedItemInput(
        source_platform="twitter",
        content_id="123",
        content_url="https://x.com/example/status/123",
        content_type="tweet",
        title="new",
        author_name="author",
        cover_url="https://example.com/cover.jpg",
    )
    row = db.upsert_saved_membership("favorite", updated, note="second")

    assert row["item_key"] == "twitter:123"
    assert row["source_platform"] == "twitter"
    assert row["content_url"] == "https://x.com/example/status/123"
    assert row["content_type"] == "tweet"
    assert row["title"] == "new"
    assert row["author_name"] == "author"
    assert row["cover_url"] == "https://example.com/cover.jpg"
    assert row["note"] == "second"


def test_legacy_watch_later_and_favorite_rows_migrate_idempotently(tmp_path: Path) -> None:
    database = Database(tmp_path / "legacy.db")
    database.initialize()
    database.conn.execute("DELETE FROM saved_sync_migrations")
    database.cache_content(
        "legacy-storage-key",
        source_platform="youtube",
        content_id="video-123",
        content_url="https://www.youtube.com/watch?v=video-123",
        content_type="video",
        title="legacy title",
        author_name="legacy author",
        cover_url="https://example.com/legacy.jpg",
    )
    database.conn.execute(
        "INSERT INTO watch_later (bvid, note) VALUES (?, ?)",
        ("legacy-storage-key", "watch note"),
    )
    database.conn.execute(
        "INSERT INTO favorites (bvid, note) VALUES (?, ?)",
        ("legacy-storage-key", "favorite note"),
    )
    database.conn.commit()

    database._ensure_saved_sync_tables()
    database._ensure_saved_sync_tables()

    watch = database.get_saved_membership("watch_later", "youtube:video-123")
    favorite = database.get_saved_membership("favorite", "youtube:video-123")
    assert watch is not None
    assert watch["title"] == "legacy title"
    assert watch["author_name"] == "legacy author"
    assert watch["note"] == "watch note"
    assert favorite is not None
    assert favorite["note"] == "favorite note"

    database.remove_saved_membership("watch_later", "youtube:video-123")
    database._ensure_saved_sync_tables()
    assert database.get_saved_membership("watch_later", "youtube:video-123") is None


def test_legacy_row_without_source_metadata_falls_back_to_bilibili(tmp_path: Path) -> None:
    database = Database(tmp_path / "legacy-bilibili.db")
    database.initialize()
    database.conn.execute("DELETE FROM saved_sync_migrations")
    database.cache_content(
        "BV1OLD",
        source_platform="   ",
        content_id="orphan-content-id",
        title="metadata without complete identity",
    )
    database.conn.execute(
        "INSERT INTO watch_later (bvid, note) VALUES (?, ?)",
        ("BV1OLD", "legacy"),
    )
    database.conn.commit()

    database._ensure_saved_sync_tables()

    row = database.get_saved_membership("watch_later", "bilibili:BV1OLD")
    assert row is not None
    assert row["content_id"] == "BV1OLD"
    assert row["source_platform"] == "bilibili"
    assert row["title"] == "metadata without complete identity"


def test_native_save_state_controls_eligibility_and_task_lookup(db: Database) -> None:
    pending = SavedItemInput("bilibili", "BV1PENDING")
    synced = SavedItemInput("bilibili", "BV1SYNCED")
    db.upsert_saved_membership("watch_later", pending)
    db.upsert_saved_membership("watch_later", synced)
    db.upsert_native_save_state(
        "watch_later",
        synced.item_key,
        requested_action="watch_later",
        resolved_action="watch_later",
        resolved_target="Bilibili watch later",
        status="synced",
        task_id="task-1",
    )

    eligible = db.list_native_sync_eligible("watch_later")
    assert [row["item_key"] for row in eligible] == [pending.item_key]
    states = db.list_native_save_states_by_task("task-1")
    assert len(states) == 1
    assert states[0]["item_key"] == synced.item_key
    assert states[0]["status"] == "synced"
    assert states[0]["title"] == ""


def test_saved_membership_methods_reject_invalid_list_kind(db: Database) -> None:
    item = SavedItemInput("bilibili", "BV1INVALID")

    with pytest.raises(ValueError, match="list_kind"):
        db.upsert_saved_membership("queue", item)
    with pytest.raises(ValueError, match="list_kind"):
        db.remove_saved_membership("queue", item.item_key)
    with pytest.raises(ValueError, match="list_kind"):
        db.get_saved_membership("queue", item.item_key)
    with pytest.raises(ValueError, match="list_kind"):
        db.list_saved_memberships("queue")
    with pytest.raises(ValueError, match="list_kind"):
        db.list_native_sync_eligible("queue")


def test_generic_bilibili_removal_also_deletes_legacy_row(db: Database) -> None:
    db.add_to_favorites("BV1REMOVE")

    assert db.remove_saved_membership("favorite", "bilibili:BV1REMOVE") is True
    assert (
        db.conn.execute("SELECT 1 FROM favorites WHERE bvid = ?", ("BV1REMOVE",)).fetchone() is None
    )
