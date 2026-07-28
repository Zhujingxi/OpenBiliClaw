"""Pure helper tests for the live unified-interest-line verifier.

The coordinator-only runner performs localhost requests and real provider work.
These canned tests deliberately exercise only count-delta and card-selection
logic; they never open a socket.
"""

from __future__ import annotations

from scripts.verify_unified_line_live import (
    compute_ledger_delta,
    select_feedback_cards,
)


def test_compute_ledger_delta_reports_increases_new_keys_and_decreases() -> None:
    before = {
        ("pipeline_layer_update", "feedback"): 2,
        ("feedback_preference_overwrite", "feedback"): 4,
        ("removed", ""): 3,
    }
    after = {
        ("pipeline_layer_update", "feedback"): 5,
        ("feedback_preference_overwrite", "feedback"): 4,
        ("new", "feedback"): 1,
    }

    assert compute_ledger_delta(before, after) == {
        ("new", "feedback"): 1,
        ("pipeline_layer_update", "feedback"): 3,
        ("removed", ""): -3,
    }


def test_select_feedback_cards_prefers_fresh_then_tops_up() -> None:
    items = [
        {"id": 1, "feedback_type": "like"},
        {"id": 2, "feedback_type": ""},
        {"id": 3},
        {"id": 4, "feedback_type": "dislike"},
    ]

    selected = select_feedback_cards(items)

    assert [item["id"] for item in selected] == [2, 3, 1]


def test_select_feedback_cards_uses_only_fresh_when_enough_exist() -> None:
    items = [
        {"id": 1, "feedback_type": "like"},
        {"id": 2},
        {"id": 3, "feedback_type": ""},
        {"id": 4, "feedback_type": None},
        {"id": 5, "feedback_type": "dislike"},
    ]

    selected = select_feedback_cards(items)

    assert [item["id"] for item in selected] == [2, 3, 4]


def test_select_feedback_cards_ignores_malformed_and_duplicate_ids() -> None:
    items = [
        None,
        {"id": True},
        {"id": 0},
        {"id": 7, "feedback_type": "like"},
        {"id": 7},
        {"id": 8},
        "not-a-card",
    ]

    selected = select_feedback_cards(items)

    assert [item["id"] for item in selected] == [8, 7]
