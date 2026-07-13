from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.saved_sync.models import SavedItemInput
from openbiliclaw.saved_sync.router import NativeSaveRouter
from openbiliclaw.saved_sync.service import SavedSyncService
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


def test_eligible_view_excludes_pending_rows_owned_by_a_task(db: Database) -> None:
    item = SavedItemInput("bilibili", "BV1OWNEDVIEW")
    db.upsert_saved_membership("favorite", item)
    db.ensure_native_save_state("favorite", item.item_key, "favorite")
    assert db.claim_native_sync_task("favorite", [item.item_key], "owned-view-task") == [
        item.item_key
    ]

    assert db.list_native_sync_eligible("favorite", [item.item_key]) == []


def test_legacy_adapter_missing_unsupported_migration_is_narrow_and_idempotent(
    db: Database,
) -> None:
    platforms_and_codes = [
        ("youtube", "unsupported"),
        ("xiaohongshu", ""),
        ("douyin", "unsupported_content_type"),
        ("bilibili", "unsupported"),
        ("unknown", "unsupported"),
    ]
    for index, (platform, error_code) in enumerate(platforms_and_codes):
        item = SavedItemInput(platform, f"legacy-{index}")
        db.upsert_saved_membership("favorite", item)
        db.upsert_native_save_state(
            "favorite",
            item.item_key,
            requested_action="favorite",
            resolved_action="favorite",
            resolved_target="",
            status="unsupported",
            last_error_code=error_code,
        )
    db.conn.execute(
        "DELETE FROM saved_sync_migrations WHERE name = ?",
        ("extension_adapter_missing_unsupported_v1",),
    )
    db.conn.commit()

    assert db.migrate_legacy_native_save_unsupported() == 2
    assert db.migrate_legacy_native_save_unsupported() == 0

    rows = {row["source_platform"]: row for row in db.list_saved_memberships("favorite", limit=20)}
    assert rows["youtube"]["last_error_code"] == "unsupported_adapter_missing"
    assert rows["xiaohongshu"]["last_error_code"] == "unsupported_adapter_missing"
    assert rows["douyin"]["last_error_code"] == "unsupported_content_type"
    assert rows["bilibili"]["last_error_code"] == "unsupported"
    assert rows["unknown"]["last_error_code"] == "unsupported"


def test_only_adapter_missing_unsupported_is_snapshot_eligible(db: Database) -> None:
    missing = SavedItemInput("youtube", "missing-adapter")
    content_type = SavedItemInput("youtube", "bad-content-type")
    for item, error_code in (
        (missing, "unsupported_adapter_missing"),
        (content_type, "unsupported_content_type"),
    ):
        db.upsert_saved_membership("favorite", item)
        db.upsert_native_save_state(
            "favorite",
            item.item_key,
            requested_action="favorite",
            resolved_action="favorite",
            resolved_target="",
            status="unsupported",
            last_error_code=error_code,
        )

    bulk = db.create_native_sync_task_snapshot("favorite", None, "bulk-missing", "manual_bulk")
    explicit_terminal = db.create_native_sync_task_snapshot(
        "favorite", [content_type.item_key], "explicit-content", "manual_single"
    )

    assert [row["item_key"] for row in bulk] == [missing.item_key]
    assert bulk[0]["status"] == "pending"
    assert explicit_terminal[0]["status"] == "unsupported"
    assert explicit_terminal[0]["is_live"] == 0


def test_live_legacy_runner_migration_is_reserved_until_task_lease_stales(
    db: Database,
) -> None:
    item = SavedItemInput("bilibili", "BV1LEGACYRUNNER")
    db.upsert_saved_membership("favorite", item)
    db.ensure_native_save_state("favorite", item.item_key, "favorite")
    assert db.claim_native_sync_task("favorite", [item.item_key], "legacy-live-task") == [
        item.item_key
    ]
    db.conn.execute(
        """
        UPDATE native_save_states
        SET task_started_at = CURRENT_TIMESTAMP,
            task_heartbeat_at = CURRENT_TIMESTAMP,
            task_runner_id = ''
        WHERE task_id = 'legacy-live-task'
        """
    )
    db.conn.commit()

    db._ensure_saved_sync_tables()

    row = db.list_native_save_states_by_task("legacy-live-task")[0]
    legacy_runner = str(row["task_runner_id"])
    assert legacy_runner.startswith("__openbiliclaw_")
    assert db.claim_native_sync_task_runner("legacy-live-task", "new-runner") is False
    with pytest.raises(ValueError, match="reserved"):
        db.claim_native_sync_task_runner("legacy-live-task", legacy_runner)

    db.conn.execute(
        """
        UPDATE native_save_states
        SET task_heartbeat_at = datetime('now', '-10 minutes')
        WHERE task_id = 'legacy-live-task'
        """
    )
    db.conn.commit()
    assert db.claim_native_sync_task_runner("legacy-live-task", "new-runner") is True
    reclaimed = db.list_native_save_states_by_task("legacy-live-task")[0]
    assert reclaimed["task_runner_id"] == "new-runner"


def test_generic_native_state_upsert_cannot_establish_active_task_ownership(
    db: Database,
) -> None:
    item = SavedItemInput("bilibili", "BV1UNSAFEUPSERT")
    db.upsert_saved_membership("favorite", item)

    with pytest.raises(ValueError, match="atomic claim"):
        db.upsert_native_save_state(
            "favorite",
            item.item_key,
            requested_action="favorite",
            status="pending",
            task_id="unsafe-task-owner",
        )
    with pytest.raises(ValueError, match="atomic claim"):
        db.upsert_native_save_state(
            "favorite",
            item.item_key,
            requested_action="favorite",
            status="syncing",
            task_id="unsafe-task-owner",
            execution_id="unsafe-execution-owner",
        )

    row = db.get_saved_membership("favorite", item.item_key)
    assert row is not None
    assert row["sync_task_id"] == ""
    assert db.list_native_save_states_by_task("unsafe-task-owner") == []


def test_native_task_dao_boundaries_reject_blank_task_ids(db: Database) -> None:
    item = SavedItemInput("bilibili", "BV1BLANKDAO")
    db.upsert_saved_membership("favorite", item)
    db.ensure_native_save_state("favorite", item.item_key, "favorite")

    with pytest.raises(ValueError, match="task_id"):
        db.list_native_save_states_by_task(" ")
    with pytest.raises(ValueError, match="task_id"):
        db.reconcile_stale_native_save_claims("")
    with pytest.raises(ValueError, match="task_id"):
        db.release_native_sync_task("\t")
    with pytest.raises(ValueError, match="task_id"):
        db.claim_native_sync_task_runner("  ", "runner")
    with pytest.raises(ValueError, match="task_id"):
        db.heartbeat_native_sync_task(" ", "runner")
    with pytest.raises(ValueError, match="task_id"):
        db.release_pending_native_sync_task("", "runner")
    with pytest.raises(ValueError, match="task_id"):
        db.release_stale_pending_native_sync_task("\t")
    with pytest.raises(ValueError, match="task_id"):
        db.claim_native_save_item("favorite", item.item_key, "", "runner", "execution")
    with pytest.raises(ValueError, match="runner_id"):
        db.claim_native_sync_task_runner("valid-task", " ")
    with pytest.raises(ValueError, match="runner_id"):
        db.heartbeat_native_sync_task("valid-task", "")
    with pytest.raises(ValueError, match="runner_id"):
        db.release_pending_native_sync_task("valid-task", "\t")


@pytest.mark.parametrize("invalid_status", ["syncing", "bogus", " pending ", "", "SYNCED"])
def test_generic_native_state_upsert_rejects_invalid_or_active_statuses(
    db: Database,
    invalid_status: str,
) -> None:
    item = SavedItemInput("bilibili", f"BV1STATUS{len(invalid_status)}{invalid_status[:1]}")
    db.upsert_saved_membership("favorite", item)

    with pytest.raises(ValueError, match="status|atomic claim"):
        db.upsert_native_save_state(
            "favorite",
            item.item_key,
            requested_action="favorite",
            status=invalid_status,
        )


def test_generic_pending_snapshot_cannot_downgrade_terminal_state(db: Database) -> None:
    item = SavedItemInput("bilibili", "BV1TERMINALDOWNGRADE")
    db.upsert_saved_membership("favorite", item)
    db.upsert_native_save_state(
        "favorite",
        item.item_key,
        requested_action="favorite",
        resolved_action="favorite",
        resolved_target="target",
        status="synced",
        task_id="terminal-task",
    )

    with pytest.raises(ValueError, match="transition"):
        db.upsert_native_save_state(
            "favorite",
            item.item_key,
            requested_action="favorite",
            status="pending",
        )

    row = db.list_native_save_states_by_task("terminal-task")[0]
    assert row["status"] == "synced"


@pytest.mark.parametrize("invalid_completion", ["pending", "syncing", "unknown", " synced "])
def test_completion_rejects_nonterminal_status_without_releasing_owner(
    db: Database,
    invalid_completion: str,
) -> None:
    item = SavedItemInput("bilibili", f"BV1COMPLETE{len(invalid_completion)}")
    db.upsert_saved_membership("favorite", item)
    db.ensure_native_save_state("favorite", item.item_key, "favorite")
    assert db.claim_native_sync_task("favorite", [item.item_key], "completion-task") == [
        item.item_key
    ]
    assert db.claim_native_sync_task_runner("completion-task", "completion-runner")
    assert db.claim_native_save_item(
        "favorite",
        item.item_key,
        "completion-task",
        "completion-runner",
        "completion-owner",
    )

    with pytest.raises(ValueError, match="terminal status"):
        db.complete_native_save_claim(
            "favorite",
            item.item_key,
            "completion-task",
            "completion-owner",
            requested_action="favorite",
            resolved_action="favorite",
            resolved_target="target",
            status=invalid_completion,
        )

    row = db.list_native_save_states_by_task("completion-task")[0]
    assert row["status"] == "syncing"
    assert row["execution_id"] == "completion-owner"


def test_atomic_task_claim_rejects_nonempty_all_blank_selection(db: Database) -> None:
    item = SavedItemInput("bilibili", "BV1DAOFILTER")
    db.upsert_saved_membership("favorite", item)

    with pytest.raises(ValueError, match="item_keys"):
        db.claim_native_sync_task("favorite", ["  ", "\t"], "must-not-own-all")

    row = db.get_saved_membership("favorite", item.item_key)
    assert row is not None
    assert row["sync_task_id"] == ""


def test_execution_heartbeat_is_fenced_by_owner_token(db: Database) -> None:
    item = SavedItemInput("bilibili", "BV1HEARTBEATFENCE")
    db.upsert_saved_membership("favorite", item)
    assert db.claim_native_sync_task("favorite", [item.item_key], "heartbeat-task") == [
        item.item_key
    ]
    assert db.claim_native_sync_task_runner("heartbeat-task", "heartbeat-runner")
    assert db.claim_native_save_item(
        "favorite",
        item.item_key,
        "heartbeat-task",
        "heartbeat-runner",
        "live-owner",
    )

    assert (
        db.heartbeat_native_save_claim("favorite", item.item_key, "heartbeat-task", "stale-owner")
        is False
    )
    assert (
        db.heartbeat_native_save_claim("favorite", item.item_key, "heartbeat-task", "live-owner")
        is True
    )


def test_native_heartbeats_do_not_share_the_process_connection(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = SavedItemInput("bilibili", "BV1HEARTBEATCONNECTION")
    db.upsert_saved_membership("favorite", item)
    assert db.claim_native_sync_task("favorite", [item.item_key], "heartbeat-connection-task") == [
        item.item_key
    ]
    assert db.claim_native_sync_task_runner("heartbeat-connection-task", "heartbeat-runner")
    assert db.claim_native_save_item(
        "favorite",
        item.item_key,
        "heartbeat-connection-task",
        "heartbeat-runner",
        "heartbeat-owner",
    )

    def reject_process_connection(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("native heartbeat used the process-wide SQLite connection")

    monkeypatch.setattr(db, "_execute_write", reject_process_connection)

    assert db.heartbeat_native_sync_task("heartbeat-connection-task", "heartbeat-runner") == 1
    assert db.heartbeat_native_save_claim(
        "favorite",
        item.item_key,
        "heartbeat-connection-task",
        "heartbeat-owner",
    )


@pytest.mark.parametrize(
    ("list_kind", "add_method_name"),
    [("watch_later", "add_to_watch_later"), ("favorite", "add_to_favorites")],
)
def test_legacy_duplicate_save_preserves_terminal_native_state(
    db: Database,
    list_kind: str,
    add_method_name: str,
) -> None:
    add_method = getattr(db, add_method_name)
    add_method("BV1LEGACYDUP")
    db.upsert_native_save_state(
        list_kind,
        "bilibili:BV1LEGACYDUP",
        requested_action=list_kind,
        resolved_action=list_kind,
        resolved_target="persisted target",
        status="already_synced",
        task_id="persisted-task",
    )

    add_method("BV1LEGACYDUP", "updated")

    row = db.get_saved_membership(list_kind, "bilibili:BV1LEGACYDUP")
    assert row is not None
    assert row["note"] == "updated"
    assert row["sync_status"] == "already_synced"
    assert row["sync_task_id"] == "persisted-task"
    assert row["resolved_target"] == "persisted target"


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


@pytest.mark.parametrize(
    ("list_kind", "legacy_table", "remove_method_name"),
    [
        ("watch_later", "watch_later", "remove_from_watch_later"),
        ("favorite", "favorites", "remove_from_favorites"),
    ],
)
def test_legacy_remove_wrapper_resolves_migrated_non_bilibili_identity(
    tmp_path: Path,
    list_kind: str,
    legacy_table: str,
    remove_method_name: str,
) -> None:
    database = Database(tmp_path / f"legacy-remove-{legacy_table}.db")
    database.initialize()
    database.conn.execute("DELETE FROM saved_sync_migrations")
    database.cache_content(
        "legacy-storage-key",
        source_platform="youtube",
        content_id="video-123",
        content_url="https://www.youtube.com/watch?v=video-123",
    )
    database.conn.execute(
        f"INSERT INTO {legacy_table} (bvid, note) VALUES (?, ?)",
        ("legacy-storage-key", "legacy note"),
    )
    database.conn.commit()
    database._ensure_saved_sync_tables()

    remove_method = getattr(database, remove_method_name)
    assert remove_method("video-123") is True
    assert database.get_saved_membership(list_kind, "youtube:video-123") is None
    assert (
        database.conn.execute(
            f"SELECT 1 FROM {legacy_table} WHERE bvid = ?", ("legacy-storage-key",)
        ).fetchone()
        is None
    )


def test_native_save_state_rejects_item_without_local_membership(db: Database) -> None:
    with pytest.raises(ValueError, match="saved membership does not exist"):
        db.upsert_native_save_state(
            "favorite",
            "youtube:not-saved",
            requested_action="favorite",
            status="pending",
            task_id="orphan-task",
        )

    assert db.list_native_save_states_by_task("orphan-task") == []
    assert (
        db.conn.execute(
            "SELECT 1 FROM native_save_states WHERE list_kind = ? AND item_key = ?",
            ("favorite", "youtube:not-saved"),
        ).fetchone()
        is None
    )


def test_legacy_remove_wrapper_fails_closed_for_ambiguous_cross_platform_id(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "legacy-remove-ambiguous.db")
    database.initialize()
    database.conn.execute("DELETE FROM saved_sync_migrations")
    database.cache_content(
        "video-123",
        source_platform="youtube",
        content_id="video-123",
    )
    database.cache_content(
        "legacy-douyin-key",
        source_platform="douyin",
        content_id="video-123",
    )
    database.conn.executemany(
        "INSERT INTO watch_later (bvid) VALUES (?)",
        [("video-123",), ("legacy-douyin-key",)],
    )
    database.conn.commit()
    database._ensure_saved_sync_tables()

    assert database.is_in_watch_later("video-123") is False
    assert database.remove_from_watch_later("video-123") is False
    assert database.get_saved_membership("watch_later", "youtube:video-123") is not None
    assert database.get_saved_membership("watch_later", "douyin:video-123") is not None
    legacy_count = database.conn.execute("SELECT COUNT(*) FROM watch_later").fetchone()
    assert legacy_count is not None
    assert int(legacy_count[0]) == 2


def _migrate_youtube_legacy_row(
    tmp_path: Path,
    legacy_table: str,
) -> Database:
    database = Database(tmp_path / f"stable-{legacy_table}.db")
    database.initialize()
    database.conn.execute("DELETE FROM saved_sync_migrations")
    database.cache_content(
        "legacy-storage-key",
        source_platform="youtube",
        content_id="video-123",
        content_url="https://www.youtube.com/watch?v=video-123",
        title="stable mapping",
    )
    database.conn.execute(
        f"INSERT INTO {legacy_table} (bvid, note) VALUES (?, ?)",
        ("legacy-storage-key", "legacy note"),
    )
    database.conn.commit()
    database._ensure_saved_sync_tables()
    database.conn.execute("DELETE FROM content_cache WHERE bvid = ?", ("legacy-storage-key",))
    database.conn.commit()
    return database


@pytest.mark.parametrize(
    ("list_kind", "legacy_table"),
    [("watch_later", "watch_later"), ("favorite", "favorites")],
)
def test_generic_remove_uses_stable_legacy_mapping_after_cache_deleted(
    tmp_path: Path,
    list_kind: str,
    legacy_table: str,
) -> None:
    database = _migrate_youtube_legacy_row(tmp_path, legacy_table)

    legacy_row = database.conn.execute(
        f"SELECT item_key FROM {legacy_table} WHERE bvid = ?", ("legacy-storage-key",)
    ).fetchone()
    assert legacy_row is not None
    assert legacy_row["item_key"] == "youtube:video-123"

    assert database.remove_saved_membership(list_kind, "youtube:video-123") is True
    assert database.get_saved_membership(list_kind, "youtube:video-123") is None
    assert database.conn.execute(f"SELECT 1 FROM {legacy_table}").fetchone() is None
    database._ensure_saved_sync_tables()
    assert database.get_saved_membership(list_kind, "youtube:video-123") is None


@pytest.mark.parametrize(
    (
        "list_kind",
        "legacy_table",
        "list_method_name",
        "status_method_name",
        "count_method_name",
        "remove_method_name",
    ),
    [
        (
            "watch_later",
            "watch_later",
            "list_watch_later",
            "is_in_watch_later",
            "count_watch_later",
            "remove_from_watch_later",
        ),
        (
            "favorite",
            "favorites",
            "list_favorites",
            "is_in_favorites",
            "count_favorites",
            "remove_from_favorites",
        ),
    ],
)
def test_legacy_wrappers_stay_consistent_for_migrated_non_bilibili_item(
    tmp_path: Path,
    list_kind: str,
    legacy_table: str,
    list_method_name: str,
    status_method_name: str,
    count_method_name: str,
    remove_method_name: str,
) -> None:
    database = _migrate_youtube_legacy_row(tmp_path, legacy_table)
    list_method = getattr(database, list_method_name)
    status_method = getattr(database, status_method_name)
    count_method = getattr(database, count_method_name)
    remove_method = getattr(database, remove_method_name)

    assert [row["bvid"] for row in list_method()] == ["video-123"]
    assert status_method("video-123") is True
    assert count_method() == 1

    assert remove_method("video-123") is True
    assert status_method("video-123") is False
    assert count_method() == 0
    assert list_method() == []
    assert database.get_saved_membership(list_kind, "youtube:video-123") is None
    assert database.conn.execute(f"SELECT 1 FROM {legacy_table}").fetchone() is None


def test_native_sync_task_snapshot_tables_are_idempotent_and_survive_membership_delete(
    db: Database,
) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1SNAPSHOT")
    service.save_local("favorite", item)
    created = service.create_sync_task("favorite", [item.item_key], "manual_single")

    db._ensure_saved_sync_tables()
    db._ensure_saved_sync_tables()
    table_names = {
        str(row["name"])
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert {"native_save_tasks", "native_save_task_items"} <= table_names

    db.remove_saved_membership("favorite", item.item_key)
    reconstructed = SavedSyncService(db, NativeSaveRouter()).get_sync_task(created.task_id)

    assert [result.item_key for result in reconstructed.items] == [item.item_key]
    assert reconstructed.items[0].status == "failed"
    assert reconstructed.items[0].error_code == "not_saved_locally"
