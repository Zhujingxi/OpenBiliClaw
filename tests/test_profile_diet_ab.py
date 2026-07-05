"""Unit tests for the profile diet A/B replay helpers."""

from __future__ import annotations

import pytest
from scripts.run_profile_diet_ab import (
    ReplayCandidate,
    _build_engine,
    _print_report,
    admission_flip_summary,
    cap_body_text,
    score_delta_summary,
    select_replay_rows,
    spearman_rank_correlation,
)

from openbiliclaw.discovery.engine import ContentDiscoveryEngine, compact_evaluation_profile_summary
from openbiliclaw.discovery.strategies._utils import build_profile_summary
from openbiliclaw.soul.profile import InterestTag, SoulProfile


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


class _ReplayDiscoveryConfig:
    multimodal_evaluation_enabled = False
    multimodal_batch_size = 8
    multimodal_image_max_px = 384
    multimodal_image_quality = 72
    multimodal_image_timeout_seconds = 6


class _ReplayConfig:
    discovery = _ReplayDiscoveryConfig()


class _ReplayEmbedding:
    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0] if text else []


def _many_interest_profile() -> SoulProfile:
    profile = SoulProfile()
    profile.preferences.interests = [
        InterestTag(name=f"兴趣{index}", category="测试", weight=1.0 - index / 1000)
        for index in range(80)
    ]
    return profile


def test_compact_replay_arm_a_forces_legacy_full_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After production becomes compact, compact-arm A must still be full-profile legacy."""

    def production_summary(profile: SoulProfile) -> dict[str, object]:
        return compact_evaluation_profile_summary(build_profile_summary(profile))

    monkeypatch.setattr(
        ContentDiscoveryEngine,
        "_evaluation_profile_summary",
        staticmethod(production_summary),
    )

    engine = _build_engine(
        object(),
        _ReplayConfig(),
        compact_profile=False,
        negative_examples=None,
        legacy_profile=True,
        embedding_service=None,
    )

    summary = engine._evaluation_profile_summary(_many_interest_profile())
    interests = summary["interests"]
    assert isinstance(interests, list)
    assert len(interests) == 80


def test_replay_engine_receives_embedding_service_for_production_recall() -> None:
    embedding = _ReplayEmbedding()

    engine = _build_engine(
        object(),
        _ReplayConfig(),
        compact_profile=True,
        negative_examples=None,
        legacy_profile=False,
        embedding_service=embedding,
    )

    assert engine._embedding_service is embedding  # noqa: SLF001


def test_replay_report_mentions_when_compact_recall_is_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_report(
        arm_b="compact",
        candidates=[ReplayCandidate(candidate_id=1, title="item", source_strategy="search")],
        scores_a=[0.7],
        scores_b=[0.7],
        platform=None,
        recall_note="related_interests recall disabled: embedding service unavailable",
    )

    output = capsys.readouterr().out
    assert "related_interests recall disabled: embedding service unavailable" in output
