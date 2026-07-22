"""Tests for the init_runs store backing guided (GUI) initialization.

See docs/specs/gui-init.md §5a and docs/plans/2026-06-07-gui-init-implementation.md A1.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
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


def test_chat_turn_payload_schema_is_present_in_fresh_database(tmp_path: Path) -> None:
    db = _db(tmp_path)

    columns = {
        str(row["name"]): str(row["dflt_value"])
        for row in db.conn.execute("PRAGMA table_info(chat_turns)").fetchall()
    }

    assert columns["payload"] == "'{}'"
    settlement_columns = {
        str(row["name"])
        for row in db.conn.execute("PRAGMA table_info(card_settlements)").fetchall()
    }
    assert {"ref", "verdict", "turn_id", "applied"} <= settlement_columns


def test_chat_turn_payload_schema_migrates_legacy_database(tmp_path: Path) -> None:
    path = tmp_path / "legacy-chat.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE chat_turns (
            turn_id TEXT PRIMARY KEY,
            session TEXT NOT NULL DEFAULT 'popup',
            scope TEXT NOT NULL DEFAULT 'chat',
            subject_id TEXT NOT NULL DEFAULT '',
            subject_title TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            reply TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO chat_turns (turn_id, message) VALUES ('legacy-turn', '旧消息');
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()

    assert "payload" in {
        str(row["name"]) for row in db.conn.execute("PRAGMA table_info(chat_turns)").fetchall()
    }
    assert db.get_chat_turn("legacy-turn")["payload"] == {}


@pytest.mark.parametrize(
    ("initial", "target"),
    [
        ("pending", "confirmed"),
        ("pending", "rejected"),
        ("pending", "deferred"),
        ("pending", "discussing"),
        ("discussing", "confirmed"),
        ("discussing", "rejected"),
        ("discussing", "deferred"),
        ("discussing", "pending"),
    ],
)
def test_chat_turn_payload_state_cas_allows_declared_transitions(
    tmp_path: Path,
    initial: str,
    target: str,
) -> None:
    db = _db(tmp_path)
    db.create_chat_turn(
        turn_id=f"{initial}-{target}",
        message="卡片",
        payload={"type": "card", "state": initial, "marker": "preserved"},
    )

    assert db.update_chat_turn_payload_state(
        f"{initial}-{target}",
        expected_state=initial,
        new_state=target,
    )
    payload = db.get_chat_turn(f"{initial}-{target}")["payload"]
    assert payload == {"type": "card", "state": target, "marker": "preserved"}


def test_chat_turn_payload_state_cas_rejects_stale_or_illegal_transition(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.create_chat_turn(
        turn_id="card-cas",
        message="卡片",
        payload={"type": "card", "state": "pending"},
    )

    assert not db.update_chat_turn_payload_state(
        "card-cas",
        expected_state="discussing",
        new_state="confirmed",
    )
    with pytest.raises(ValueError, match="Unsupported card payload transition"):
        db.update_chat_turn_payload_state(
            "card-cas",
            expected_state="confirmed",
            new_state="pending",
        )
    assert db.get_chat_turn("card-cas")["payload"]["state"] == "pending"


def test_card_settlement_insert_or_ignore_arbitrates_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "settlement.db"
    first = Database(path)
    first.initialize()
    second = Database(path)
    second.initialize()
    barrier = threading.Barrier(2)

    def contend(db: Database, verdict: str, turn_id: str) -> bool:
        barrier.wait()
        return db.try_create_card_settlement(
            ref="hypothesis:abc12345",
            verdict=verdict,
            turn_id=turn_id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda args: contend(*args),
                [(first, "confirmed", "turn-confirm"), (second, "rejected", "turn-reject")],
            )
        )

    assert sorted(outcomes) == [False, True]
    settlement = first.get_card_settlement("hypothesis:abc12345")
    assert settlement is not None
    assert (settlement["verdict"], settlement["turn_id"]) in {
        ("confirmed", "turn-confirm"),
        ("rejected", "turn-reject"),
    }
    assert settlement["applied"] == 0


def test_chat_turn_list_uses_rowid_for_equal_created_at(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.create_chat_turn(turn_id="z-first", message="先插入")
    db.create_chat_turn(turn_id="a-second", message="后插入")
    db.conn.execute("UPDATE chat_turns SET created_at = '2026-07-22 01:00:00'")
    db.conn.commit()

    rows = db.list_chat_turns(limit=2)

    assert [row["turn_id"] for row in rows] == ["z-first", "a-second"]


def test_get_latest_init_run_none_when_empty(tmp_path: Path) -> None:
    assert _db(tmp_path).get_latest_init_run() is None


def test_init_runs_migrates_separate_progress_clock(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE init_runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            stage INTEGER NOT NULL DEFAULT 0,
            stages_json TEXT,
            partial_success INTEGER NOT NULL DEFAULT 0,
            error_reason TEXT,
            error_detail TEXT,
            sequence INTEGER NOT NULL DEFAULT 0,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        );
        INSERT INTO init_runs (run_id, status, sequence, updated_at)
        VALUES ('legacy-run', 'completed', 7, '2026-07-01 01:02:03');
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()
    run = db.get_latest_init_run()
    assert run["progress_sequence"] == 0
    assert str(run["progress_at"]) == "2026-07-01 01:02:03"


def test_init_run_reserve_and_roundtrip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.try_reserve_init_starting("run-1") is True

    run = db.get_latest_init_run()
    assert run is not None
    assert run["run_id"] == "run-1"
    assert run["status"] == "starting"
    assert run["stage"] == 0
    assert run["partial_success"] == 0
    assert run["progress_sequence"] == 0
    assert run["progress_at"] is not None

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
    db.update_init_run(
        "run-1",
        status="running",
        stage=3,
        stages_json=json.dumps(
            [
                {"n": 1, "status": "ok", "reason": None},
                {"n": 2, "status": "running", "reason": None, "progress": 0.5},
                {"n": 3, "status": "pending", "reason": None},
            ]
        ),
    )

    reconciled = db.reconcile_init_runs_on_boot()
    assert reconciled == 1

    run = db.get_latest_init_run()
    assert run["status"] == "failed"
    assert run["error_reason"] == "interrupted"
    assert run["finished_at"] is not None

    # A user-facing detail is written so /api/init-status is diagnosable.
    assert run["error_detail"] == "初始化后台任务已结束，但未能写入终态；已自动释放运行锁。"

    # Running/pending stages are downgraded to failed/interrupted (no phantom
    # "running" stage survives a restart); completed stages are left intact.
    stages = json.loads(run["stages_json"])
    assert stages[0]["status"] == "ok"
    assert stages[1]["status"] == "failed"
    assert stages[1]["reason"] == "interrupted"
    assert "progress" not in stages[1]
    assert stages[2]["status"] == "failed"
    assert stages[2]["reason"] == "interrupted"

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


def test_login_state_writes_are_safe_across_concurrent_fastapi_threads(tmp_path: Path) -> None:
    """XHS and Zhihu heartbeats arrive together on runtime-stream connect."""
    db = _db(tmp_path)

    def write_login_state(index: int) -> None:
        logged_in = index % 2 == 0
        if index % 2 == 0:
            db.set_xhs_login_state(logged_in)
        else:
            db.set_zhihu_login_state(logged_in)

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(write_login_state, index) for index in range(200)]
        for future in futures:
            future.result()

    assert db.get_xhs_login_state()[1]
    assert db.get_zhihu_login_state()[1]


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


# --------------------------------------------------------------------------
# Confusion objects (Phase 2)
# --------------------------------------------------------------------------


def test_insert_and_get_confusion_roundtrips(tmp_path: Path) -> None:
    db = _db(tmp_path)
    cid = db.insert_confusion(
        source="awareness",
        topic="解压视频",
        observation="连续看解压视频但停留很短",
        interpretation="可能是背景音而非兴趣",
        interpretation_confidence=0.4,
        evidence_refs=["note-1", "note-2"],
    )
    assert cid > 0
    row = db.get_confusion(cid)
    assert row is not None
    assert row["status"] == "open"
    assert row["topic"] == "解压视频"
    assert row["evidence_refs"] == ["note-1", "note-2"]
    assert row["held_updates"] == []


def test_list_confusions_filters_by_status(tmp_path: Path) -> None:
    db = _db(tmp_path)
    a = db.insert_confusion(topic="a")
    db.insert_confusion(topic="b")
    db.update_confusion(a, status="resolved", resolution="real_interest")
    assert {r["topic"] for r in db.list_confusions(statuses=["open"])} == {"b"}
    assert {r["topic"] for r in db.list_confusions(statuses=["resolved"])} == {"a"}


def test_claim_confusion_clarifying_atomic_single_winner(tmp_path: Path) -> None:
    db = _db(tmp_path)
    a = db.insert_confusion(topic="a")
    b = db.insert_confusion(topic="b")
    assert db.claim_confusion_clarifying(a, ask_turn_id="t1") is True
    # Second claim (different row) violates the partial unique index → False.
    assert db.claim_confusion_clarifying(b, ask_turn_id="t2") is False
    assert db.get_confusion(a)["status"] == "clarifying"
    assert db.get_confusion(b)["status"] == "open"
    # Re-claiming an already-clarifying row is a no-op False (not 'open').
    assert db.claim_confusion_clarifying(a, ask_turn_id="t3") is False


def test_claim_confusion_clarifying_cross_connection(tmp_path: Path) -> None:
    path = tmp_path / "confusion_race.db"
    db0 = Database(path)
    db0.initialize()
    a = db0.insert_confusion(topic="a")
    b = db0.insert_confusion(topic="b")

    def _claim(cid: int, turn: str) -> bool:
        db = Database(path)
        db.initialize()
        try:
            return db.claim_confusion_clarifying(cid, ask_turn_id=turn)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_claim, a, "t1")
        f2 = pool.submit(_claim, b, "t2")
        results = [f1.result(), f2.result()]

    # Exactly one connection wins the single clarifying slot.
    assert results.count(True) == 1
    clarifying = db0.list_confusions(statuses=["clarifying"])
    assert len(clarifying) == 1


def test_update_confusion_rejects_unknown_column(tmp_path: Path) -> None:
    db = _db(tmp_path)
    cid = db.insert_confusion(topic="a")
    with pytest.raises(ValueError, match="Unknown confusion column"):
        db.update_confusion(cid, bogus="x")
