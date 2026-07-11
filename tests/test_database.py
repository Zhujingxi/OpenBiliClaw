"""Tests for the init_runs store backing guided (GUI) initialization.

See docs/specs/gui-init.md §5a and docs/plans/2026-06-07-gui-init-implementation.md A1.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "init.db")
    db.initialize()
    return db


def test_get_latest_init_run_none_when_empty(tmp_path: Path) -> None:
    assert _db(tmp_path).get_latest_init_run() is None


def test_init_run_reserve_and_roundtrip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.try_reserve_init_starting("run-1") is True

    run = db.get_latest_init_run()
    assert run is not None
    assert run["run_id"] == "run-1"
    assert run["status"] == "starting"
    assert run["stage"] == 0
    assert run["partial_success"] == 0

    db.update_init_run(
        "run-1",
        status="running",
        stage=2,
        sequence=5,
        stages_json=json.dumps([{"n": 1, "status": "ok"}, {"n": 2, "status": "running"}]),
    )
    run = db.get_latest_init_run()
    assert run["status"] == "running"
    assert run["stage"] == 2
    assert run["sequence"] == 5
    assert json.loads(run["stages_json"])[0]["status"] == "ok"


def test_try_reserve_is_single_flight(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.try_reserve_init_starting("run-1") is True
    # A second reservation while one is active must fail (TOCTOU guard).
    assert db.try_reserve_init_starting("run-2") is False

    # Once the active run finishes, a new run can be reserved again.
    db.update_init_run("run-1", status="completed")
    assert db.try_reserve_init_starting("run-3") is True
    assert db.get_latest_init_run()["run_id"] == "run-3"


def test_reconcile_fails_stale_active_runs_on_boot(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.try_reserve_init_starting("run-1")
    db.update_init_run("run-1", status="running", stage=3)

    reconciled = db.reconcile_init_runs_on_boot()
    assert reconciled == 1

    run = db.get_latest_init_run()
    assert run["status"] == "failed"
    assert run["error_reason"] == "interrupted"
    assert run["finished_at"] is not None

    # Idempotent: a completed run is not touched a second time.
    assert db.reconcile_init_runs_on_boot() == 0


def test_reconcile_leaves_terminal_runs_untouched(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.try_reserve_init_starting("run-1")
    db.update_init_run("run-1", status="completed")
    assert db.reconcile_init_runs_on_boot() == 0
    assert db.get_latest_init_run()["status"] == "completed"


def test_update_init_run_rejects_unknown_column(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.try_reserve_init_starting("run-1")
    with pytest.raises(ValueError, match="unknown columns"):
        db.update_init_run("run-1", bogus="x")


def test_xhs_login_state_roundtrips_through_auth_state(tmp_path: Path) -> None:
    db = _db(tmp_path)

    assert db.get_xhs_login_state() == (False, "")

    db.set_xhs_login_state(True, when_iso="2026-07-07T01:02:03+00:00")
    assert db.get_xhs_login_state() == (True, "2026-07-07T01:02:03+00:00")
    assert (
        db.conn.execute("SELECT value FROM auth_state WHERE key = 'xhs_login_state'").fetchone()[0]
        == "1"
    )
    assert (
        db.conn.execute("SELECT value FROM auth_state WHERE key = 'xhs_login_state_at'").fetchone()[
            0
        ]
        == "2026-07-07T01:02:03+00:00"
    )

    db.set_xhs_login_state(False, when_iso="2026-07-07T02:03:04+00:00")
    assert db.get_xhs_login_state() == (False, "2026-07-07T02:03:04+00:00")
    assert (
        db.conn.execute("SELECT value FROM auth_state WHERE key = 'xhs_login_state'").fetchone()[0]
        == "0"
    )


def test_zhihu_login_state_roundtrips_through_auth_state(tmp_path: Path) -> None:
    db = _db(tmp_path)

    assert db.get_zhihu_login_state() == (False, "")

    db.set_zhihu_login_state(True, when_iso="2026-07-07T03:04:05+00:00")
    assert db.get_zhihu_login_state() == (True, "2026-07-07T03:04:05+00:00")
    assert (
        db.conn.execute("SELECT value FROM auth_state WHERE key = 'zhihu_login_state'").fetchone()[
            0
        ]
        == "1"
    )
    assert (
        db.conn.execute(
            "SELECT value FROM auth_state WHERE key = 'zhihu_login_state_at'"
        ).fetchone()[0]
        == "2026-07-07T03:04:05+00:00"
    )

    db.set_zhihu_login_state(False, when_iso="2026-07-07T04:05:06+00:00")
    assert db.get_zhihu_login_state() == (False, "2026-07-07T04:05:06+00:00")
    assert (
        db.conn.execute("SELECT value FROM auth_state WHERE key = 'zhihu_login_state'").fetchone()[
            0
        ]
        == "0"
    )


def test_get_recommendations_rows_carry_card_metadata_columns(tmp_path: Path) -> None:
    """Regression (issue #75): the history join must SELECT the card-metadata
    columns, otherwise /api/recommendations serializes them all as 0 even
    though content_cache has real values (stub-based endpoint tests can't
    catch a missing SQL column)."""
    db = _db(tmp_path)
    db.cache_content(
        "BV1meta",
        title="元信息视频",
        up_name="某UP",
        up_mid=12345,
        duration=3723,
        view_count=120000,
        like_count=4567,
        danmaku_count=890,
        favorite_count=321,
        comment_count=654,
        cover_url="https://example.com/cover.jpg",
        source_platform="bilibili",
        content_type="video",
        published_at="2026-07-08T06:30:00Z",
        published_label="3 天前",
        relevance_score=0.9,
    )
    db.insert_recommendation("BV1meta", confidence=0.9, expression="试试", topic="测试")

    rows = db.get_recommendations(limit=10)

    assert len(rows) == 1
    row = rows[0]
    assert row["duration"] == 3723
    assert row["view_count"] == 120000
    assert row["like_count"] == 4567
    assert row["danmaku_count"] == 890
    assert row["favorite_count"] == 321
    assert row["comment_count"] == 654
    assert row["up_mid"] == 12345
    assert row["published_at"] == "2026-07-08T06:30:00Z"
    assert row["published_label"] == "3 天前"


@pytest.mark.parametrize(
    ("incoming_at", "incoming_label", "expected_at", "expected_label"),
    [
        ("", "更新后的相对时间", "2026-07-08T06:30:00Z", "更新后的相对时间"),
        ("2026-07-09T06:30:00Z", "", "2026-07-09T06:30:00Z", "旧标签"),
    ],
)
def test_content_cache_rediscovery_preserves_each_empty_publication_field_independently(
    tmp_path: Path,
    incoming_at: str,
    incoming_label: str,
    expected_at: str,
    expected_label: str,
) -> None:
    db = _db(tmp_path)
    db.cache_content(
        "BV1TIME",
        title="A",
        published_at="2026-07-08T06:30:00Z",
        published_label="旧标签",
    )
    db.cache_content(
        "BV1TIME",
        title="A",
        published_at=incoming_at,
        published_label=incoming_label,
    )

    row = db.conn.execute(
        "SELECT published_at, published_label FROM content_cache WHERE bvid='BV1TIME'"
    ).fetchone()

    assert row["published_at"] == expected_at
    assert row["published_label"] == expected_label


def test_legacy_content_tables_gain_publication_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "init.db"
    db = Database(db_path)
    db.initialize()
    db.cache_content("BV1LEGACY", title="legacy content")
    candidate = DiscoveredContent(bvid="BV1LEGACY-CANDIDATE", title="legacy candidate")
    candidate_write = discovered_content_to_candidate_write(candidate)
    db.enqueue_discovery_candidates([candidate_write])
    for table_name in ("content_cache", "discovery_candidates"):
        existing = {
            str(row["name"])
            for row in db.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name in ("published_at", "published_label"):
            if column_name in existing:
                db.conn.execute(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")
    db.conn.commit()
    db.close()

    migrated = Database(db_path)
    migrated.initialize()

    for table_name in ("content_cache", "discovery_candidates"):
        columns = {
            str(row["name"]): row
            for row in migrated.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name in ("published_at", "published_label"):
            assert columns[column_name]["notnull"] == 1
            assert columns[column_name]["dflt_value"] == "''"
    content = migrated.conn.execute(
        "SELECT title, published_at, published_label FROM content_cache WHERE bvid = ?",
        ("BV1LEGACY",),
    ).fetchone()
    assert dict(content) == {
        "title": "legacy content",
        "published_at": "",
        "published_label": "",
    }
    candidate_row = migrated.conn.execute(
        "SELECT title, published_at, published_label "
        "FROM discovery_candidates WHERE candidate_key = ?",
        (candidate_write.candidate_key,),
    ).fetchone()
    assert dict(candidate_row) == {
        "title": "legacy candidate",
        "published_at": "",
        "published_label": "",
    }
    migrated.close()
