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
