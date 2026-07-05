"""Tests for the init_runs store backing guided (GUI) initialization.

See docs/specs/gui-init.md §5a and docs/plans/2026-06-07-gui-init-implementation.md A1.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

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
        cover_url="https://example.com/cover.jpg",
        source_platform="bilibili",
        content_type="video",
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
    assert row["up_mid"] == 12345


# --- recent_event_urls (cross-source dedup helper) -------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402


def _insert_event_with_age(
    db: Database,
    *,
    event_type: str,
    url: str,
    source: str = "extension",
    age_hours: float = 1.0,
) -> int:
    metadata: dict[str, object] = {"source": source} if source else {}
    row_id = db.insert_event(
        event_type,
        url=url,
        title="title",
        context="",
        metadata=metadata,
    )
    created = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=age_hours)).isoformat(
        sep=" "
    )
    db.conn.execute("UPDATE events SET created_at = ? WHERE id = ?", (created, row_id))
    db.conn.commit()
    return row_id


def test_recent_event_urls_returns_recent_view_urls_only(tmp_path: Path) -> None:
    db = _db(tmp_path)
    recent = "https://www.bilibili.com/video/BVRECENT"
    old = "https://www.bilibili.com/video/BVOLD"
    other_type = "https://www.bilibili.com/video/BVFAV"
    _insert_event_with_age(db, event_type="view", url=recent, age_hours=1.0)
    _insert_event_with_age(db, event_type="view", url=old, age_hours=72.0)
    _insert_event_with_age(db, event_type="favorite", url=other_type, age_hours=1.0)

    urls = db.recent_event_urls(["view"], within_hours=48)

    assert urls == {recent}


def test_recent_event_urls_excludes_empty_urls(tmp_path: Path) -> None:
    db = _db(tmp_path)
    good = "https://www.bilibili.com/video/BVGOOD"
    _insert_event_with_age(db, event_type="view", url=good, age_hours=1.0)
    _insert_event_with_age(db, event_type="view", url="", age_hours=1.0)

    urls = db.recent_event_urls(["view"], within_hours=48)

    assert urls == {good}


def test_recent_event_urls_respects_limit(tmp_path: Path) -> None:
    db = _db(tmp_path)
    for i, age in enumerate((3.0, 2.0, 1.0)):
        _insert_event_with_age(
            db,
            event_type="view",
            url=f"https://www.bilibili.com/video/BV{i}",
            age_hours=age,
        )

    urls = db.recent_event_urls(["view"], within_hours=48, limit=2)

    # Newest two only (created_at DESC ordering, SQL LIMIT applied).
    assert len(urls) == 2
    assert "https://www.bilibili.com/video/BV0" not in urls


def test_recent_event_urls_exclude_source_drops_matching_rows(tmp_path: Path) -> None:
    db = _db(tmp_path)
    extension_url = "https://www.bilibili.com/video/BVEXT"
    account_sync_url = "https://www.bilibili.com/video/BVACC"
    _insert_event_with_age(db, event_type="view", url=extension_url, source="extension")
    _insert_event_with_age(db, event_type="view", url=account_sync_url, source="account_sync")

    urls = db.recent_event_urls(["view"], within_hours=48, exclude_source="account_sync")

    assert urls == {extension_url}
