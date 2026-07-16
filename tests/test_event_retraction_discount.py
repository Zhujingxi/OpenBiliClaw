"""Tests for retraction determinstic discounting (event-capture-completion Phase 0).

Covers three faces:
- DB reread path: ``Database.mark_positive_events_retracted`` (Task 0).
- In-memory pipeline: ``ProfileUpdatePipeline`` atomic discount + tombstone (Task 1).
- Render marker + replay invariance (Task 2).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


# Epoch-ms helpers so event time vs retraction_at causality is explicit.
_T0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _make_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "retraction.db")
    db.initialize()
    return db


def _metadata_for(db: Database, row_id: int) -> dict[str, object]:
    cursor = db.conn.execute("SELECT metadata FROM events WHERE id = ?", (row_id,))
    raw = cursor.fetchone()["metadata"]
    return json.loads(raw)


# --- Task 0: DB reread path --------------------------------------------------


def test_mark_retracted_discounts_matching_like_by_identity_key(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    # Positive like reported at the /i/status form (earlier than retraction).
    row_id = db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        title="ext like",
        metadata={"source_platform": "twitter", "signal_strength": 0.85, "timestamp": _ms(_T0)},
    )

    # Retraction arrives under the /<handle>/status form — different URL, same key.
    marked = db.mark_positive_events_retracted(
        ["https://x.com/someuser/status/123"],
        "like",
        retraction_at=_T0 + timedelta(hours=1),
    )

    assert marked == 1
    meta = _metadata_for(db, row_id)
    assert meta["retracted"] is True
    assert meta["signal_strength"] == 0.2


def test_mark_retracted_zero_hits_returns_zero(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        metadata={"timestamp": _ms(_T0)},
    )
    marked = db.mark_positive_events_retracted(
        ["https://x.com/i/status/999"],
        "like",
        retraction_at=_T0 + timedelta(hours=1),
    )
    assert marked == 0


def test_mark_retracted_does_not_touch_other_event_types(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    fav_id = db.insert_event(
        "favorite",
        url="https://x.com/i/status/123",
        metadata={"signal_strength": 1.0, "timestamp": _ms(_T0)},
    )
    marked = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"],
        "like",  # action is like → favorite row must be untouched
        retraction_at=_T0 + timedelta(hours=1),
    )
    assert marked == 0
    assert "retracted" not in _metadata_for(db, fav_id)


def test_mark_retracted_is_idempotent(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    row_id = db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        metadata={"signal_strength": 0.85, "timestamp": _ms(_T0)},
    )
    at = _T0 + timedelta(hours=1)
    first = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "like", retraction_at=at
    )
    second = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "like", retraction_at=at
    )
    assert first == 1
    assert second == 1  # already-retracted rows still match; strength stays capped
    assert _metadata_for(db, row_id)["signal_strength"] == 0.2


def test_mark_retracted_preserves_row_count_and_non_metadata_columns(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    row_id = db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        title="original title",
        metadata={"signal_strength": 0.85, "timestamp": _ms(_T0)},
    )
    before = db.conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
    db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "like", retraction_at=_T0 + timedelta(hours=1)
    )
    after_rows = db.conn.execute(
        "SELECT event_type, url, title FROM events WHERE id = ?", (row_id,)
    ).fetchone()
    after_count = db.conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
    assert before == after_count == 1
    assert after_rows["event_type"] == "like"
    assert after_rows["url"] == "https://x.com/i/status/123"
    assert after_rows["title"] == "original title"


def test_mark_retracted_all_four_key_types(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    note = "0123456789abcdef01234567"
    cases = [
        ("like", "https://x.com/i/status/123", "https://x.com/u/status/123"),
        (
            "favorite",
            "https://www.bilibili.com/video/BV1a",
            "https://www.bilibili.com/video/BV1a?p=2",
        ),
        ("follow", "https://space.bilibili.com/42", "https://space.bilibili.com/42/dynamic"),
        (
            "like",
            f"https://www.xiaohongshu.com/explore/{note}",
            f"https://www.xiaohongshu.com/discovery/item/{note}",
        ),
    ]
    for action, positive_url, retraction_url in cases:
        row_id = db.insert_event(
            action,
            url=positive_url,
            metadata={"signal_strength": 0.9, "timestamp": _ms(_T0)},
        )
        marked = db.mark_positive_events_retracted(
            [retraction_url], action, retraction_at=_T0 + timedelta(hours=1)
        )
        assert marked == 1, f"{action} {positive_url}"
        assert _metadata_for(db, row_id)["retracted"] is True


def test_mark_retracted_skips_events_after_retraction_time(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    # Re-like AFTER the retraction (like -> retract -> like): must not be marked.
    row_id = db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        metadata={"signal_strength": 0.85, "timestamp": _ms(_T0 + timedelta(hours=2))},
    )
    marked = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "like", retraction_at=_T0 + timedelta(hours=1)
    )
    assert marked == 0
    assert "retracted" not in _metadata_for(db, row_id)


def test_mark_retracted_skips_events_missing_timestamp(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    row_id = db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        metadata={"signal_strength": 0.85},  # no timestamp → conservatively skip
    )
    marked = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "like", retraction_at=_T0 + timedelta(hours=1)
    )
    assert marked == 0
    assert "retracted" not in _metadata_for(db, row_id)


def test_mark_retracted_rejects_out_of_whitelist_action(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    db.insert_event("view", url="https://x.com/i/status/123", metadata={"timestamp": _ms(_T0)})
    marked = db.mark_positive_events_retracted(
        ["https://x.com/i/status/123"], "view", retraction_at=_T0 + timedelta(hours=1)
    )
    assert marked == 0
