"""Unit tests for the profile diet A/B replay helpers."""

from __future__ import annotations

import pytest
from scripts.run_profile_diet_ab import (
    ReplayCandidate,
    admission_flip_summary,
    cap_body_text,
    score_delta_summary,
    select_replay_rows,
    spearman_rank_correlation,
)


def test_score_delta_summary_reports_mean_and_nearest_rank_p95() -> None:
    summary = score_delta_summary([0.20, 0.60, 0.90, 0.40], [0.10, 0.65, 0.70, 0.40])

    assert summary.mean_abs_delta == pytest.approx(0.0875)
    assert summary.p95_abs_delta == pytest.approx(0.20)


def test_spearman_rank_correlation_handles_ordering_and_ties() -> None:
    assert spearman_rank_correlation([0.1, 0.2, 0.3, 0.4], [1.0, 2.0, 3.0, 4.0]) == pytest.approx(
        1.0
    )
    assert spearman_rank_correlation([0.1, 0.2, 0.3, 0.4], [4.0, 3.0, 2.0, 1.0]) == pytest.approx(
        -1.0
    )
    assert spearman_rank_correlation([0.5, 0.5, 0.9], [0.4, 0.4, 0.8]) == pytest.approx(1.0)


def test_admission_flip_summary_uses_default_strategy_thresholds() -> None:
    candidates = [
        ReplayCandidate(candidate_id=1, title="search drops", source_strategy="search"),
        ReplayCandidate(candidate_id=2, title="explore rises", source_strategy="explore"),
        ReplayCandidate(candidate_id=3, title="unknown rises", source_strategy="custom"),
        ReplayCandidate(candidate_id=4, title="stable admitted", source_strategy="hot"),
    ]

    summary = admission_flip_summary(
        candidates,
        [0.61, 0.57, 0.59, 0.70],
        [0.59, 0.59, 0.61, 0.68],
    )

    assert summary.flip_count == 3
    assert summary.flip_rate == pytest.approx(0.75)
    assert summary.per_strategy == {"custom": 1, "explore": 1, "search": 1}


def test_select_replay_rows_filters_status_platform_and_orders_deterministically() -> None:
    rows = [
        {
            "id": 1,
            "status": "evaluated",
            "source_platform": "bilibili",
            "evaluated_at": "2026-07-04 10:00:00",
            "last_seen_at": "2026-07-04 10:00:00",
        },
        {
            "id": 2,
            "status": "cached",
            "source_platform": "xiaohongshu",
            "evaluated_at": "2026-07-04 11:00:00",
            "last_seen_at": "2026-07-04 11:00:00",
        },
        {
            "id": 3,
            "status": "evaluated",
            "source_platform": "bilibili",
            "evaluated_at": "2026-07-04 12:00:00",
            "last_seen_at": "2026-07-04 12:00:00",
        },
        {
            "id": 4,
            "status": "evaluated",
            "source_platform": "bilibili",
            "evaluated_at": "2026-07-04 12:00:00",
            "last_seen_at": "2026-07-04 12:00:00",
        },
        {
            "id": 5,
            "status": "pending_eval",
            "source_platform": "bilibili",
            "evaluated_at": "2026-07-05 12:00:00",
            "last_seen_at": "2026-07-05 12:00:00",
        },
    ]

    selected = select_replay_rows(rows, sample=3, platform="bilibili")

    assert [row["id"] for row in selected] == [4, 3, 1]


def test_select_replay_rows_interleaves_platform_strategy_groups() -> None:
    """The gate sample must stay mixed, not collapse onto the most recent wave."""
    rows = []
    for index in range(6):
        rows.append(
            {
                "id": 100 + index,
                "status": "cached",
                "source_platform": "reddit",
                "source_strategy": "subreddit",
                "evaluated_at": f"2026-07-05 12:0{index}:00",
            }
        )
    rows.append(
        {
            "id": 200,
            "status": "cached",
            "source_platform": "bilibili",
            "source_strategy": "search",
            "evaluated_at": "2026-07-01 08:00:00",
        }
    )

    selected = select_replay_rows(rows, sample=4)

    platforms = {row["source_platform"] for row in selected}
    assert platforms == {"reddit", "bilibili"}
    assert len(selected) == 4
    # Deterministic: same input -> same output.
    assert [row["id"] for row in select_replay_rows(rows, sample=4)] == [
        row["id"] for row in selected
    ]


def test_cap_body_text_keeps_short_text_and_caps_long_text() -> None:
    short = "short body"
    long = "h" * 1700 + "m" * 300 + "t" * 500

    assert cap_body_text(short) == short
    assert cap_body_text(long) == ("h" * 1600) + "\u2026" + ("t" * 400)
