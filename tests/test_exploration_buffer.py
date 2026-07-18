from __future__ import annotations

from datetime import UTC, datetime, timedelta

from openbiliclaw.soul.exploration_buffer import (
    make_buffer_entry,
    pop_promotable_buffer_entries,
    record_buffer_event,
)


def test_buffer_promotes_after_three_explicit_weak_events_with_score_threshold() -> None:
    now = datetime(2026, 5, 24, tzinfo=UTC)
    state = record_buffer_event(
        {},
        domain="城市基础设施观察",
        source_event="weak_positive_chat",
        now=now,
    )
    state = record_buffer_event(
        state,
        domain="城市基础设施观察",
        source_event="card_like",
        now=now + timedelta(days=1),
    )
    state = record_buffer_event(
        state,
        domain="城市基础设施观察",
        source_event="card_more_like",
        now=now + timedelta(days=2),
    )

    promoted, state = pop_promotable_buffer_entries(state, now=now + timedelta(days=2))

    assert promoted[0]["domain"] == "城市基础设施观察"
    assert promoted[0]["confirmation_source"] == "buffer_promoted"
    assert state["entries"] == []


def test_buffer_cooldown_ignores_positive_score_increments() -> None:
    now = datetime(2026, 5, 24, tzinfo=UTC)
    state = record_buffer_event(
        {},
        domain="城市基础设施观察",
        source_event="negative",
        now=now,
    )
    state = record_buffer_event(
        state,
        domain="城市基础设施观察",
        source_event="card_like",
        now=now + timedelta(hours=1),
    )

    entry = state["entries"][0]
    assert entry["score"] == -3
    assert entry["positive_event_count"] == 0


def test_buffer_expiry_is_later_than_promotion_window() -> None:
    now = datetime(2026, 5, 24, tzinfo=UTC)
    entry = make_buffer_entry(domain="x", first_seen=now)

    assert entry["expires_at"] == (now + timedelta(days=10)).isoformat()
