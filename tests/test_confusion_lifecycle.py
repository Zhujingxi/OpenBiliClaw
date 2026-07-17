"""Confusion object lifecycle tests (Phase 2 — Wave B).

Covers the confusion state machine, the two producing sources (awareness +
speculation stalemate), the DB-level clarifying budget, TTL expiry, and (Task 5)
the ask/three-exit clarification paths, topic freeze, and held-update replay
state machine with crash-recovery idempotency.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
from openbiliclaw.soul.confusion import (
    MAX_CONFUSION_CANDIDATES_PER_ROUND,
    Confusion,
    ConfusionManager,
    HeldUpdate,
)
from openbiliclaw.soul.speculator import (
    SpeculativeInterest,
    _stalemate_from_rejected,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "confusion.db")
    db.initialize()
    return db


# --------------------------------------------------------------------------
# Producing source 1: awareness candidates
# --------------------------------------------------------------------------


def test_create_from_awareness_candidates_persists_and_validates(tmp_path: Path) -> None:
    mgr = ConfusionManager(_db(tmp_path))
    ids = mgr.create_from_awareness_candidates(
        [
            {
                "topic": "解压视频",
                "observation": "连续点开但停留很短",
                "interpretation": "可能是背景音",
                "interpretation_confidence": 0.3,
                "evidence_refs": ["note-1"],
            },
            {"observation": "", "topic": "空的"},  # dropped: no observation
        ]
    )
    assert len(ids) == 1
    stored = mgr.get(ids[0])
    assert stored is not None
    assert stored.topic == "解压视频"
    assert stored.status == "open"
    assert stored.evidence_refs == ["note-1"]


def test_create_from_awareness_candidates_caps_batch(tmp_path: Path) -> None:
    mgr = ConfusionManager(_db(tmp_path))
    cands = [
        {"observation": f"obs-{i}", "topic": f"t{i}"}
        for i in range(MAX_CONFUSION_CANDIDATES_PER_ROUND + 3)
    ]
    ids = mgr.create_from_awareness_candidates(cands)
    assert len(ids) == MAX_CONFUSION_CANDIDATES_PER_ROUND


# --------------------------------------------------------------------------
# Producing source 2: speculation stalemate
# --------------------------------------------------------------------------


def test_stalemate_filter_only_partial_confirmations() -> None:
    zero = SpeculativeInterest(domain="a", confirmation_count=0, confirmation_threshold=3)
    partial = SpeculativeInterest(domain="b", confirmation_count=2, confirmation_threshold=3)
    at_thresh = SpeculativeInterest(domain="c", confirmation_count=3, confirmation_threshold=3)
    result = _stalemate_from_rejected([zero, partial, at_thresh])
    assert [s.domain for s in result] == ["b"]


def test_create_from_speculation_stalemate_persists(tmp_path: Path) -> None:
    mgr = ConfusionManager(_db(tmp_path))
    cid = mgr.create_from_speculation_stalemate(
        domain="桌游",
        confirmation_count=2,
        confirmation_threshold=3,
    )
    assert cid is not None
    stored = mgr.get(cid)
    assert stored is not None
    assert stored.source == "speculation_stalemate"
    assert stored.topic == "桌游"


# --------------------------------------------------------------------------
# Awareness analyzer parse of {"notes", "confusions"}
# --------------------------------------------------------------------------


class _FakeRegistry:
    def __init__(self, content: str) -> None:
        self._content = content

    async def complete_structured_task(self, **kwargs: object):  # type: ignore[no-untyped-def]
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(content=self._content, model="fake", provider="fake")


async def test_analyze_with_confusions_parses_both() -> None:
    payload = """
    {
      "notes": [
        {"date": "2026-07-17", "observation": "看深度内容",
         "trend": "偏深度", "emotion_guess": "专注"}
      ],
      "confusions": [
        {"topic": "解压", "observation": "停留很短", "interpretation": "背景音",
         "interpretation_confidence": 0.3, "evidence_refs": []},
        {"observation": "", "topic": "空"}
      ]
    }
    """
    analyzer = AwarenessAnalyzer(_FakeRegistry(payload))
    notes, confusions = await analyzer.analyze_with_confusions(
        events=[{"id": 5, "event_type": "view", "title": "x"}],
        preference={},
        soul_profile={},
    )
    assert len(notes) == 1
    assert notes[0].source_event_ids == [5]
    assert len(confusions) == 1  # empty-observation dropped
    assert confusions[0]["topic"] == "解压"


async def test_analyze_with_confusions_tolerates_legacy_array() -> None:
    payload = '[{"observation": "只有笔记", "date": "2026-07-17"}]'
    analyzer = AwarenessAnalyzer(_FakeRegistry(payload))
    notes, confusions = await analyzer.analyze_with_confusions(
        events=[], preference={}, soul_profile={}
    )
    assert len(notes) == 1
    assert confusions == []


# --------------------------------------------------------------------------
# TTL expiry
# --------------------------------------------------------------------------


def test_expire_due_expires_past_ttl(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    past = (datetime.now() - timedelta(days=1)).isoformat()
    future = (datetime.now() + timedelta(days=1)).isoformat()
    stale = db.insert_confusion(topic="stale", observation="x", expires_at=past)
    fresh = db.insert_confusion(topic="fresh", observation="y", expires_at=future)
    no_ttl = db.insert_confusion(topic="notl", observation="z")
    expired = mgr.expire_due()
    assert expired == [stale]
    assert mgr.get(stale).status == "expired"
    assert mgr.get(fresh).status == "open"
    assert mgr.get(no_ttl).status == "open"


# --------------------------------------------------------------------------
# Dataclass round-trip
# --------------------------------------------------------------------------


def test_confusion_from_row_decodes_held_updates(tmp_path: Path) -> None:
    db = _db(tmp_path)
    cid = db.insert_confusion(topic="a", observation="x")
    held = HeldUpdate(held_id="h1", topic="a", kind="upgrade", value=0.8, prev_value=0.5)
    db.update_confusion(cid, held_updates=[held.to_dict()])
    confusion = Confusion.from_row(db.get_confusion(cid))
    assert len(confusion.held_updates) == 1
    assert confusion.held_updates[0].held_id == "h1"
    assert confusion.held_updates[0].kind == "upgrade"


# --------------------------------------------------------------------------
# Task 5: ask scheduling + 72h cooldown
# --------------------------------------------------------------------------


def test_schedule_ask_claims_and_enforces_cooldown(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    now = datetime(2026, 7, 17, 12, 0, 0)
    assert mgr.schedule_ask(cid, ask_turn_id="t1", now=now) is True
    assert mgr.get(cid).status == "clarifying"
    # Defer releases the slot but keeps asked_at (cooldown persists).
    mgr.defer(cid, now=now)
    assert mgr.get(cid).status == "open"
    # Re-ask within 72h is refused.
    soon = now + timedelta(hours=10)
    assert mgr.schedule_ask(cid, ask_turn_id="t2", now=soon) is False
    # After 72h it is allowed again.
    later = now + timedelta(hours=73)
    assert mgr.schedule_ask(cid, ask_turn_id="t3", now=later) is True


def test_schedule_ask_single_clarifying_budget(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    a = db.insert_confusion(topic="a", observation="x")
    b = db.insert_confusion(topic="b", observation="y")
    assert mgr.schedule_ask(a, ask_turn_id="t1") is True
    # Second ask blocked by the single clarifying slot.
    assert mgr.schedule_ask(b, ask_turn_id="t2") is False


def test_cooldown_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "cooldown.db"
    db = Database(path)
    db.initialize()
    mgr = ConfusionManager(db)
    now = datetime(2026, 7, 17, 12, 0, 0)
    cid = db.insert_confusion(topic="a", observation="x")
    assert mgr.schedule_ask(cid, ask_turn_id="t1", now=now) is True
    mgr.defer(cid, now=now)
    db.close()
    # Fresh connection (restart) — cooldown still blocks.
    db2 = Database(path)
    db2.initialize()
    mgr2 = ConfusionManager(db2)
    assert mgr2.schedule_ask(cid, ask_turn_id="t2", now=now + timedelta(hours=1)) is False


# --------------------------------------------------------------------------
# Task 5: three resolution exits
# --------------------------------------------------------------------------


def test_resolve_real_interest_begins_held_replay(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="桌游", observation="x")
    held = HeldUpdate(held_id="h1", topic="桌游", kind="upgrade", value=0.8, prev_value=0.4)
    db.update_confusion(cid, held_updates=[held.to_dict()])
    assert mgr.resolve(cid, resolution="real_interest") == "resolved"
    restored = mgr.get(cid)
    assert restored.status == "resolved"
    only = restored.held_updates[0]
    assert only.state == "replaying"
    assert only.replay_submitted_at  # receipt persisted in the same txn
    assert only.batch_id


def test_resolve_proxy_discards_held(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    db.update_confusion(cid, held_updates=[HeldUpdate(held_id="h1", topic="a").to_dict()])
    assert mgr.resolve(cid, resolution="proxy_behavior") == "resolved"
    assert mgr.get(cid).held_updates[0].state == "discarded"


def test_resolve_dismissed_status_and_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    assert mgr.resolve(cid, resolution="dismissed") == "dismissed"
    # Idempotent — a terminal confusion is not re-resolved.
    assert mgr.resolve(cid, resolution="real_interest") == "dismissed"


def test_resolve_rejects_bad_resolution(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    assert mgr.resolve(cid, resolution="bogus") is None
    assert mgr.get(cid).status == "open"


# --------------------------------------------------------------------------
# Task 5: topic freeze
# --------------------------------------------------------------------------


def test_apply_confusion_freeze_noop_when_no_frozen_topics() -> None:
    from openbiliclaw.soul.confusion import apply_confusion_freeze

    before = {"interests": [{"name": "a", "category": "c", "weight": 0.3}]}
    after = {"interests": [{"name": "a", "category": "c", "weight": 0.9}]}
    result, held = apply_confusion_freeze(before=before, after=after, frozen_topics=set())
    assert result is after
    assert held == []


def test_apply_confusion_freeze_holds_new_and_upgrade() -> None:
    from openbiliclaw.soul.confusion import apply_confusion_freeze

    before = {"interests": [{"name": "frozen", "category": "c", "weight": 0.3}]}
    after = {
        "interests": [
            {"name": "frozen", "category": "c", "weight": 0.9},  # upgrade → hold delta
            {"name": "newfrozen", "category": "c", "weight": 0.7},  # new → hold entirely
            {"name": "normal", "category": "c", "weight": 0.5},  # passes through
        ]
    }
    result, held = apply_confusion_freeze(
        before=before, after=after, frozen_topics={"frozen", "newfrozen"}
    )
    names = {i["name"]: i["weight"] for i in result["interests"]}
    assert names["frozen"] == 0.3  # existing weight preserved (not rolled forward)
    assert "newfrozen" not in names  # new frozen topic dropped from the write
    assert names["normal"] == 0.5
    kinds = {h.topic: h.kind for h in held}
    assert kinds == {"frozen": "upgrade", "newfrozen": "new"}


def test_record_held_updates_attaches_to_active_confusion(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="frozen", observation="x")
    mgr.record_held_updates(
        [HeldUpdate(held_id="h1", topic="frozen", kind="upgrade", value=0.9, prev_value=0.3)]
    )
    stored = mgr.get(cid)
    assert len(stored.held_updates) == 1
    assert stored.held_updates[0].held_id == "h1"


def test_frozen_topics_reflects_active_only(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    db.insert_confusion(topic="open_topic", observation="x")
    resolved = db.insert_confusion(topic="done_topic", observation="y")
    mgr.resolve(resolved, resolution="dismissed")
    assert mgr.frozen_topics() == {"open_topic"}


# --------------------------------------------------------------------------
# Task 5: held-update replay recovery (crash idempotency, r5/R4-1)
# --------------------------------------------------------------------------


def test_recover_replaying_with_receipt_marks_applied_unverified(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    held = HeldUpdate(held_id="h1", topic="a")
    db.update_confusion(cid, held_updates=[held.to_dict()])
    mgr.resolve(cid, resolution="real_interest")  # → replaying + receipt
    # Simulate crash: applied never ran. Recovery must NOT resubmit.
    touched = mgr.recover_replaying()
    assert cid in touched
    only = mgr.get(cid).held_updates[0]
    assert only.state == "applied_unverified"


def test_recover_replaying_no_receipt_retries_then_discards(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    # Defensive branch: replaying without a receipt (should not normally happen).
    held = HeldUpdate(held_id="h1", topic="a", state="replaying", replay_attempts=1)
    db.update_confusion(
        cid, status="resolved", resolution="real_interest", held_updates=[held.to_dict()]
    )
    mgr.recover_replaying()
    assert mgr.get(cid).held_updates[0].state == "held"  # retry (attempts 1 < 2)
    # Now at the attempt ceiling → discarded.
    held2 = HeldUpdate(held_id="h1", topic="a", state="replaying", replay_attempts=2)
    db.update_confusion(cid, held_updates=[held2.to_dict()])
    mgr.recover_replaying()
    assert mgr.get(cid).held_updates[0].state == "discarded"


def test_mark_replay_applied_transitions(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    db.update_confusion(cid, held_updates=[HeldUpdate(held_id="h1", topic="a").to_dict()])
    mgr.resolve(cid, resolution="real_interest")
    mgr.mark_replay_applied(cid)
    assert mgr.get(cid).held_updates[0].state == "applied"
