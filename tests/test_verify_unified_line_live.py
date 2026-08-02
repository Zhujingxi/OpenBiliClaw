"""Pure helper tests for the live unified-interest-line verifier.

The coordinator-only runner performs localhost requests and real provider work.
These canned tests deliberately exercise only count-delta and card-selection
logic; they never open a socket.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from scripts.verify_unified_line_live import (
    FeedbackOwnerCheckpoint,
    compute_ledger_delta,
    content_feedback_checkpoint,
    read_content_feedback_checkpoint,
    select_feedback_cards,
)

if TYPE_CHECKING:
    from pathlib import Path


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


def test_content_feedback_checkpoint_reads_only_pipeline_consumer_maps() -> None:
    state = {
        # Deliberately conflicting legacy-mirror fields: these are provenance,
        # not authority, and must never affect the verifier verdict.
        "feedback_owner_version": 99,
        "last_processed_feedback_event_id": 999,
        "consumer_cursors": {"content_feedback": 42, "profile_events": 88},
        "consumer_owner_versions": {"content_feedback": 2},
        "consumer_cutover_at": {"content_feedback": "2026-08-01T01:02:03"},
        "consumer_cutover_event_ids": {"content_feedback": 40},
    }

    assert content_feedback_checkpoint(state) == FeedbackOwnerCheckpoint(
        cursor=42,
        owner_version=2,
        cutover_at="2026-08-01T01:02:03",
        cutover_event_id=40,
        has_cutover_event_id=True,
    )


def test_content_feedback_checkpoint_handles_malformed_or_absent_maps() -> None:
    assert (
        content_feedback_checkpoint(
            {
                "consumer_cursors": {"content_feedback": "bad"},
                "consumer_owner_versions": [],
                "consumer_cutover_at": None,
                "consumer_cutover_event_ids": {"profile_events": 7},
            }
        )
        == FeedbackOwnerCheckpoint()
    )


def test_read_content_feedback_checkpoint_uses_pipeline_state_file(tmp_path: Path) -> None:
    pipeline_path = tmp_path / "pipeline_state.json"
    pipeline_path.write_text(
        json.dumps(
            {
                "consumer_cursors": {"content_feedback": 17},
                "consumer_owner_versions": {"content_feedback": 2},
                "consumer_cutover_at": {"content_feedback": "cutover"},
                "consumer_cutover_event_ids": {"content_feedback": 12},
            }
        ),
        encoding="utf-8",
    )

    assert read_content_feedback_checkpoint(pipeline_path) == FeedbackOwnerCheckpoint(
        cursor=17,
        owner_version=2,
        cutover_at="cutover",
        cutover_event_id=12,
        has_cutover_event_id=True,
    )
