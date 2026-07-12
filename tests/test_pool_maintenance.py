"""Regression tests for atomic, availability-safe pool maintenance."""

from pathlib import Path
from typing import Any

import pytest

from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.storage import database as database_module
from openbiliclaw.storage.database import Database


def _database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "pool-maintenance.db")
    db.initialize()
    return db


def _seed_ready(
    db: Database,
    bvid: str,
    *,
    topic_group: str,
    source: str = "search",
    source_platform: str = "bilibili",
    content_url: str | None = None,
) -> None:
    db.cache_content(
        bvid,
        title=f"Ready {bvid}",
        source=source,
        source_platform=source_platform,
        content_url=content_url or f"https://www.bilibili.com/video/{bvid}",
        relevance_score=0.9,
        pool_expression="测试推荐文案",
        pool_topic_label="测试主题",
        style_key="tutorial",
        topic_group=topic_group,
    )


def _seed_unready(
    db: Database,
    bvid: str,
    *,
    topic_group: str,
    source: str = "search",
    source_platform: str = "bilibili",
    content_url: str | None = None,
) -> None:
    db.cache_content(
        bvid,
        title=f"Raw {bvid}",
        source=source,
        source_platform=source_platform,
        content_url=content_url or f"https://www.bilibili.com/video/{bvid}",
        relevance_score=0.9,
        topic_group=topic_group,
    )


def _enqueue_candidates(db: Database, count: int, *, prefix: str = "candidate") -> list[int]:
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:{prefix}-{index}",
                source_platform="bilibili",
                source_strategy="search",
                content_id=f"{prefix}-{index}",
                title=f"Candidate {index}",
            )
            for index in range(count)
        ]
    )
    return [
        int(row["id"])
        for row in db.conn.execute(
            "SELECT id FROM discovery_candidates WHERE candidate_key LIKE ? ORDER BY id",
            (f"bilibili:{prefix}-%",),
        ).fetchall()
    ]


def _candidate_state(db: Database) -> list[tuple[int, str, str | None, str | None]]:
    return [
        (int(row["id"]), str(row["status"]), row["claim_token"], row["eval_error"])
        for row in db.conn.execute(
            "SELECT id, status, claim_token, eval_error FROM discovery_candidates ORDER BY id"
        ).fetchall()
    ]


class _BeginImmediateFailure:
    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    def execute(self, sql: str, *_: Any) -> None:
        assert sql == "BEGIN IMMEDIATE"
        raise database_module.sqlite3.OperationalError("database is locked")

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_user_a_shape_raw_trim_cannot_erase_sixteen_available(tmp_path: Path) -> None:
    db = _database(tmp_path)
    for index in range(16):
        _seed_ready(db, f"BV_READY_{index:03d}", topic_group=f"ready-{index}")
    for index in range(602):
        _seed_unready(db, f"BV_RAW_{index:03d}", topic_group=f"raw-{index % 5}")

    before = db.count_pool_candidates()
    result = db.maintain_pool_inventory(
        target=600,
        raw_ceiling=600,
        source_share_quotas={"bilibili": 5},
        raw_source_share_quotas={"bilibili": 600},
        max_per_topic_group=3,
    )

    assert before == 16
    assert result.available_before == 16
    assert result.available_after >= 16
    assert result.raw_before == 618
    assert result.raw_after == 600
    assert result.rolled_back is False


def test_user_b_source_trim_defers_to_ten_available_zhihu_rows(tmp_path: Path) -> None:
    db = _database(tmp_path)
    sources = ("zhihu-creator", "zhihu-hot", "zhihu-feed", "zhihu-related")
    for index in range(10):
        _seed_ready(
            db,
            f"ZH_READY_{index:03d}",
            topic_group=f"ready-{index}",
            source=sources[index % len(sources)],
            source_platform="zhihu",
            content_url=f"https://www.zhihu.com/question/1/answer/{index + 1}",
        )
    for index in range(12):
        _seed_unready(
            db,
            f"ZH_RAW_{index:03d}",
            topic_group=f"raw-{index}",
            source=sources[index % len(sources)],
            source_platform="zhihu",
            content_url=f"https://www.zhihu.com/question/2/answer/{index + 1}",
        )

    result = db.maintain_pool_inventory(
        target=10,
        raw_ceiling=10,
        source_share_quotas={"zhihu": 3},
        raw_source_share_quotas={"zhihu": 10},
    )

    assert result.available_before == 10
    assert result.available_after == 10
    assert result.trimmed_raw == 12
    assert result.deferred_source_trim >= 7
    assert db.count_pool_available_candidates_by_source() == {"zhihu": 10}


def test_cross_table_raw_trim_preserves_claims_and_prefers_pending(tmp_path: Path) -> None:
    db = _database(tmp_path)
    for index in range(4):
        _seed_ready(db, f"BV_READY_{index}", topic_group=f"ready-{index}")
    for index in range(3):
        _seed_unready(db, f"BV_RAW_{index}", topic_group=f"raw-{index}")
    evaluating_ids = _enqueue_candidates(db, 2, prefix="owned")
    claimed = db.claim_discovery_candidates_for_eval(limit=2, claim_token="owned-token")
    assert {int(row["id"]) for row in claimed} == set(evaluating_ids)
    candidate_ids = _enqueue_candidates(db, 6)
    db.conn.execute(
        "UPDATE discovery_candidates SET status='evaluated' WHERE id IN (?, ?)",
        (candidate_ids[4], candidate_ids[5]),
    )
    db.conn.commit()
    total_rows_before = int(
        db.conn.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0]
    )

    result = db.maintain_pool_inventory(
        target=4,
        raw_ceiling=8,
        source_share_quotas={"bilibili": 4},
        raw_source_share_quotas={"bilibili": 8},
    )

    statuses = db.count_discovery_candidates_by_status()
    pending_ids = candidate_ids[:4]
    pending_placeholders = ", ".join("?" for _ in pending_ids)
    pending_statuses = {
        str(row["status"])
        for row in db.conn.execute(
            f"SELECT status FROM discovery_candidates WHERE id IN ({pending_placeholders})",
            pending_ids,
        ).fetchall()
    }
    assert result.available_after == 4
    assert result.raw_after == 8
    assert statuses["evaluating"] == 2
    assert statuses["trimmed_capacity"] >= 1
    assert pending_statuses == {"trimmed_capacity"}
    total_rows_after = int(
        db.conn.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0]
    )
    assert total_rows_after == total_rows_before
    assert {
        str(row["claim_token"])
        for row in db.conn.execute(
            "SELECT claim_token FROM discovery_candidates WHERE status='evaluating'"
        ).fetchall()
    } == {"owned-token"}


def test_source_queue_cap_ignores_terminal_history_and_terminalizes_excess(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path)
    candidate_ids = _enqueue_candidates(db, 6, prefix="source-cap")
    db.conn.execute(
        "UPDATE discovery_candidates SET status='rejected_low_score' WHERE id=?",
        (candidate_ids[0],),
    )
    db.conn.commit()
    claimed = db.claim_discovery_candidates_for_eval(limit=2, claim_token="source-owner")
    assert len(claimed) == 2
    before = _candidate_state(db)

    trimmed = db.trim_discovery_candidates_for_source(
        source_platform="bilibili",
        max_pending=3,
    )

    after = _candidate_state(db)
    active_after = [row for row in after if row[1] in {"pending_eval", "evaluating", "evaluated"}]
    assert trimmed == 2
    assert len(after) == len(before)
    assert len(active_after) == 3
    assert sum(row[1] == "trimmed_capacity" for row in after) == 2
    assert {row[2] for row in after if row[1] == "evaluating"} == {"source-owner"}
    assert {row[3] for row in after if row[1] == "trimmed_capacity"} == {
        "source_raw_ceiling:bilibili"
    }


def test_available_surplus_only_trims_down_to_target(tmp_path: Path) -> None:
    db = _database(tmp_path)
    for index in range(16):
        _seed_ready(db, f"BV_SURPLUS_{index}", topic_group=f"ready-{index}")

    result = db.maintain_pool_inventory(
        target=10,
        raw_ceiling=10,
        source_share_quotas={"bilibili": 10},
        raw_source_share_quotas={"bilibili": 10},
    )

    assert result.available_before == 16
    assert result.available_after == 10
    assert result.trimmed_ready_reserve == 6
    assert result.rolled_back is False


def test_invariant_failure_rolls_back_every_victim_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _database(tmp_path)
    for index in range(4):
        _seed_ready(db, f"BV_READY_{index}", topic_group=f"ready-{index}")
    for index in range(2):
        _seed_unready(db, f"BV_RAW_{index}", topic_group=f"raw-{index}")
    candidate_ids = _enqueue_candidates(db, 3, prefix="rollback")
    claimed = db.claim_discovery_candidates_for_eval(limit=1, claim_token="rollback-owner")
    assert len(claimed) == 1
    content_before = {
        str(row["bvid"]): str(row["pool_status"])
        for row in db.conn.execute(
            "SELECT bvid, pool_status FROM content_cache ORDER BY bvid"
        ).fetchall()
    }
    candidates_before = _candidate_state(db)

    def _force_failure(**_: Any) -> None:
        raise database_module.PoolMaintenanceInvariantError("forced test failure")

    monkeypatch.setattr(db, "_validate_pool_maintenance_invariant", _force_failure)

    result = db.maintain_pool_inventory(
        target=4,
        raw_ceiling=4,
        source_share_quotas={"bilibili": 4},
        raw_source_share_quotas={"bilibili": 4},
    )

    content_after = {
        str(row["bvid"]): str(row["pool_status"])
        for row in db.conn.execute(
            "SELECT bvid, pool_status FROM content_cache ORDER BY bvid"
        ).fetchall()
    }
    assert result.rolled_back is True
    assert result.reason == "forced test failure"
    assert content_after == content_before
    assert _candidate_state(db) == candidates_before
    assert candidate_ids


def test_begin_immediate_failure_does_not_fabricate_zero_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _database(tmp_path)
    _seed_ready(db, "BV_LOCKED_READY", topic_group="ready")
    failing_connection = _BeginImmediateFailure()
    monkeypatch.setattr(db, "open_connection", lambda: failing_connection)
    snapshot_error = getattr(
        database_module,
        "PoolMaintenanceSnapshotUnavailableError",
        RuntimeError,
    )

    with pytest.raises(snapshot_error, match="snapshot unavailable"):
        db.maintain_pool_inventory(
            target=1,
            raw_ceiling=2,
            source_share_quotas={"bilibili": 1},
        )

    assert db.count_pool_candidates() == 1
    assert failing_connection.rolled_back is True
    assert failing_connection.closed is True
