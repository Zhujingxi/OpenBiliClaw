"""Dialogue-anchor lifecycle contract for confirmation conversations."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.soul.dialogue_anchor import (
    ENTRY_CARD_DISCUSS,
    ENTRY_CONFUSION_PROMPT,
    ENTRY_PENDING_OPEN,
    DialogueAnchorManager,
)
from openbiliclaw.soul.ledger import ProfileLedger
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


_START = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)


def _database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "anchor.db")
    db.initialize()
    return db


def _manager(
    tmp_path: Path,
    *,
    db: Database | None = None,
    now: datetime = _START,
) -> DialogueAnchorManager:
    database = db or _database(tmp_path)
    return DialogueAnchorManager(
        tmp_path,
        database=database,
        ledger=ProfileLedger(database),
        now_provider=lambda: now,
    )


@pytest.mark.parametrize(
    "entry",
    [ENTRY_CONFUSION_PROMPT, ENTRY_CARD_DISCUSS, ENTRY_PENDING_OPEN],
)
def test_anchor_can_be_established_from_each_declared_entry(
    tmp_path: Path,
    entry: str,
) -> None:
    manager = _manager(tmp_path)

    anchor = manager.establish(
        kind="hypothesis",
        ref="abc12345",
        origin_turn_id="origin-1",
        entry=entry,
    )

    assert anchor.kind == "hypothesis"
    assert anchor.ref == "abc12345"
    assert anchor.generation == 1
    assert anchor.origin_turn_id == "origin-1"
    assert anchor.unrelated_streak == 0
    assert anchor.ambiguous_count == 0


def test_anchor_replacement_keeps_only_one_and_increments_generation(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first = manager.establish(
        kind="hypothesis",
        ref="first",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    second = manager.establish(
        kind="confusion",
        ref="2",
        origin_turn_id="question-2",
        entry=ENTRY_CONFUSION_PROMPT,
    )

    assert first.generation == 1
    assert second.generation == 2
    assert manager.current() == second
    rows = manager.database.query_profile_ledger(days=1, limit=10)
    assert [row["write_point"] for row in rows[:2]] == [
        "anchor_established",
        "anchor_released",
    ]
    assert json.loads(rows[1]["after_summary"])["reason"] == "replaced"


def test_reestablishing_identical_anchor_is_idempotent(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first = manager.establish(
        kind="hypothesis",
        ref="same",
        origin_turn_id="origin",
        entry=ENTRY_CARD_DISCUSS,
    )

    retried = manager.establish(
        kind="hypothesis",
        ref="same",
        origin_turn_id="origin",
        entry=ENTRY_CARD_DISCUSS,
    )

    assert retried == first
    assert len(manager.database.query_profile_ledger(days=1)) == 1


def test_settlement_releases_anchor(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.establish(
        kind="confusion",
        ref="7",
        origin_turn_id="question-7",
        entry=ENTRY_CONFUSION_PROMPT,
    )

    released = manager.release(reason="settled")

    assert released is not None
    assert manager.current() is None


def test_two_consecutive_unrelated_turns_release_anchor(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.establish(
        kind="hypothesis",
        ref="topic",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    after_first = manager.note_relation("unrelated")
    after_second = manager.note_relation("unrelated")

    assert after_first is not None and after_first.unrelated_streak == 1
    assert after_second is None
    assert manager.current() is None


def test_related_turn_breaks_unrelated_streak(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.establish(
        kind="hypothesis",
        ref="topic",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    manager.note_relation("unrelated")
    related = manager.note_relation("support")
    still_active = manager.note_relation("unrelated")

    assert related is not None and related.unrelated_streak == 0
    assert still_active is not None and still_active.unrelated_streak == 1


def test_ttl_releases_at_two_hours_but_not_before(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.establish(
        kind="hypothesis",
        ref="ttl",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    assert manager.expire(now=_START + timedelta(hours=2) - timedelta(seconds=1)) is False
    assert manager.current() is not None
    assert manager.expire(now=_START + timedelta(hours=2)) is True
    assert manager.current() is None


@pytest.mark.parametrize("reason", ["replaced", "ttl", "unrelated"])
def test_non_settlement_confusion_release_reopens_clarifying_slot(
    tmp_path: Path,
    reason: str,
) -> None:
    db = _database(tmp_path)
    confusion_id = db.insert_confusion(topic="桌游", observation="待澄清")
    assert db.claim_confusion_clarifying(
        confusion_id,
        ask_turn_id="question",
        asked_at=_START.isoformat(),
    )
    manager = _manager(tmp_path, db=db)
    manager.establish(
        kind="confusion",
        ref=str(confusion_id),
        origin_turn_id="question",
        entry=ENTRY_CONFUSION_PROMPT,
    )

    if reason == "replaced":
        manager.establish(
            kind="hypothesis",
            ref="replacement",
            origin_turn_id="",
            entry=ENTRY_PENDING_OPEN,
        )
    elif reason == "ttl":
        assert manager.expire(now=_START + timedelta(hours=2))
    else:
        manager.note_relation("unrelated")
        manager.note_relation("unrelated")

    assert db.get_confusion(confusion_id)["status"] == "open"
    other_id = db.insert_confusion(topic="电影", observation="另一个")
    assert db.claim_confusion_clarifying(other_id, ask_turn_id="other", asked_at="now")


def test_anchor_survives_restart_with_original_ttl_clock(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    established = manager.establish(
        kind="hypothesis",
        ref="persisted",
        origin_turn_id="origin",
        entry=ENTRY_CARD_DISCUSS,
    )

    restarted = DialogueAnchorManager(
        tmp_path,
        database=manager.database,
        ledger=ProfileLedger(manager.database),
        now_provider=lambda: _START + timedelta(hours=1),
    )

    assert restarted.current() == established
    assert not restarted.expire(now=_START + timedelta(hours=1, minutes=59))
    assert restarted.expire(now=_START + timedelta(hours=2))


def test_stale_generation_snapshot_is_dropped_with_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = _manager(tmp_path)
    old = manager.establish(
        kind="hypothesis",
        ref="old",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )
    current = manager.establish(
        kind="hypothesis",
        ref="new",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    with caplog.at_level("WARNING"):
        assert manager.validate_snapshot(old.ref, old.generation) is None

    assert manager.validate_snapshot(current.ref, current.generation) == current
    assert "stale dialogue anchor snapshot" in caplog.text


def test_settlement_patches_origin_card_terminal_state(tmp_path: Path) -> None:
    db = _database(tmp_path)
    db.create_chat_turn(
        turn_id="card-origin",
        message="聊聊这个",
        payload={"type": "card", "state": "discussing"},
    )
    manager = _manager(tmp_path, db=db)
    manager.establish(
        kind="hypothesis",
        ref="card-ref",
        origin_turn_id="card-origin",
        entry=ENTRY_CARD_DISCUSS,
    )

    manager.release(reason="settled", card_state="confirmed")

    assert db.get_chat_turn("card-origin")["payload"]["state"] == "confirmed"


def test_non_settlement_release_returns_origin_card_to_pending(tmp_path: Path) -> None:
    db = _database(tmp_path)
    db.create_chat_turn(
        turn_id="card-origin",
        message="聊聊这个",
        payload={"type": "card", "state": "discussing"},
    )
    manager = _manager(tmp_path, db=db)
    manager.establish(
        kind="hypothesis",
        ref="card-ref",
        origin_turn_id="card-origin",
        entry=ENTRY_CARD_DISCUSS,
    )

    manager.note_relation("unrelated")
    manager.note_relation("unrelated")

    assert db.get_chat_turn("card-origin")["payload"]["state"] == "pending"


def test_ambiguous_count_accumulates_and_non_ambiguous_resets(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.establish(
        kind="hypothesis",
        ref="ambiguous",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    first = manager.note_relation("ambiguous")
    second = manager.note_relation("ambiguous")
    reset = manager.note_relation("support")

    assert first is not None and first.ambiguous_count == 1
    assert second is not None and second.ambiguous_count == 2
    assert reset is not None and reset.ambiguous_count == 0


def test_generation_remains_monotonic_after_release(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first = manager.establish(
        kind="confusion",
        ref="1",
        origin_turn_id="question",
        entry=ENTRY_CONFUSION_PROMPT,
    )
    manager.release(reason="settled")

    second = manager.establish(
        kind="hypothesis",
        ref="next",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    assert second.generation == first.generation + 1
