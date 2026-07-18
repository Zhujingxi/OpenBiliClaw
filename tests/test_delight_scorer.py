"""Tests for the delight threshold policy and the delight storage queries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _make_database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.initialize()
    return db


# ---------------------------------------------------------------------------
# Threshold behavior
# ---------------------------------------------------------------------------


def test_effective_threshold_raises_for_conservative_users() -> None:
    from openbiliclaw.recommendation.delight import effective_delight_threshold

    assert effective_delight_threshold(0.6, threshold=0.70) == 0.70
    assert effective_delight_threshold(0.2, threshold=0.70) == 0.80  # Conservative user


def test_default_thresholds_keep_evo_delight_bar_high() -> None:
    """Delight copy should only be generated for high-confidence Evo fits."""
    from openbiliclaw.recommendation.delight import (
        CONSERVATIVE_DELIGHT_THRESHOLD,
        DEFAULT_DELIGHT_THRESHOLD,
    )

    assert DEFAULT_DELIGHT_THRESHOLD == 0.75
    assert CONSERVATIVE_DELIGHT_THRESHOLD == 0.80
    # And the conservative bar must remain strictly above the default.
    assert CONSERVATIVE_DELIGHT_THRESHOLD > DEFAULT_DELIGHT_THRESHOLD


def test_score_065_rejected_at_default_threshold() -> None:
    """A 0.65 Evo relevance score must NOT receive proactive delight copy."""
    from openbiliclaw.recommendation.delight import effective_delight_threshold

    threshold = effective_delight_threshold(exploration_openness=0.5)
    assert threshold > 0.65, (
        f"effective_threshold={threshold} would admit score=0.65, but that is below "
        "the proactive delight copy bar."
    )


# ---------------------------------------------------------------------------
# Database — delight columns
# ---------------------------------------------------------------------------


def test_database_delight_columns_exist_after_init(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    columns = {
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(content_cache)").fetchall()
    }
    assert "delight_score" in columns
    assert "delight_reason" in columns
    assert "delight_hook" in columns
    assert "delight_notified" in columns
    assert "delight_notified_at" in columns


def test_database_update_and_get_delight_candidate(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1DL", title="惊喜内容", relevance_score=0.9)
    database.update_delight_score(
        "BV1DL",
        delight_score=0.92,
        delight_reason="这条会戳到你的深层需求",
        delight_hook="深层共鸣",
    )

    candidate = database.get_delight_candidate(min_delight_score=0.85)

    assert candidate is not None
    assert candidate["bvid"] == "BV1DL"
    assert candidate["delight_score"] == 0.92
    assert candidate["delight_reason"] == "这条会戳到你的深层需求"
    assert candidate["delight_hook"] == "深层共鸣"


def test_database_get_delight_candidate_returns_none_below_threshold(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1LOW", title="普通内容", relevance_score=0.5)
    database.update_delight_score(
        "BV1LOW",
        delight_score=0.3,
        delight_reason="",
        delight_hook="",
    )

    candidate = database.get_delight_candidate(min_delight_score=0.85)

    assert candidate is None


def test_database_get_delight_candidate_requires_ready_copy(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1BLANK", title="只有分数没有文案", relevance_score=0.9)
    database.update_delight_score(
        "BV1BLANK",
        delight_score=0.92,
        delight_reason="",
        delight_hook="",
    )

    candidate = database.get_delight_candidate(min_delight_score=0.70)

    assert candidate is None


def test_database_get_delight_candidate_excludes_suppressed_pool_items(
    tmp_path: Path,
) -> None:
    """Suppressed items must NOT surface as delight.

    A previous version of this test asserted the opposite (suppressed
    delight items are still surfaced, with the rationale "虽然普通池压
    掉了，但这条对你还是很可能是惊喜"). In practice this caused 20
    stale "delights" to appear on every popup reload — items that had
    been trimmed out by topic-group caps or source-quota balancing
    months ago, with delight scores baked under earlier looser
    calibrations. After the v0.3.32 dislike/threshold recalibration,
    9991 such ghosts were sitting on the suppressed graveyard.
    Restricting to ``pool_status IN ('fresh', 'shown')`` keeps delight
    in lockstep with the active pool.
    """
    database = _make_database(tmp_path)
    database.cache_content(
        "BV1SUPPRESS",
        title="被普通池压下去的惊喜内容",
        relevance_score=0.92,
    )
    database.conn.execute(
        "UPDATE content_cache SET pool_status = 'suppressed' WHERE bvid = ?",
        ("BV1SUPPRESS",),
    )
    database.conn.commit()
    database.update_delight_score(
        "BV1SUPPRESS",
        delight_score=0.91,
        delight_reason="历史评分残留，应当被新规则过滤。",
        delight_hook="压箱惊喜",
    )

    candidate = database.get_delight_candidate(min_delight_score=0.70)

    assert candidate is None


def test_database_mark_delight_notified(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1DLN", title="已通知", relevance_score=0.9)
    database.update_delight_score(
        "BV1DLN",
        delight_score=0.95,
        delight_reason="reason",
        delight_hook="hook",
    )
    database.mark_delight_notified("BV1DLN")

    # Should not appear since it's already notified
    candidate = database.get_delight_candidate(min_delight_score=0.85)
    assert candidate is None


def test_database_delight_candidates_skip_feedbacked_items(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1LIKE", title="已反馈", relevance_score=0.9)
    database.cache_content("BV1FRESH", title="新惊喜", relevance_score=0.9)
    database.cache_content("BV1HATE", title="已点不感兴趣", relevance_score=0.9)
    database.update_delight_score(
        "BV1LIKE",
        delight_score=0.95,
        delight_reason="liked reason",
        delight_hook="liked hook",
    )
    database.update_delight_score(
        "BV1FRESH",
        delight_score=0.94,
        delight_reason="fresh reason",
        delight_hook="fresh hook",
    )
    database.update_delight_score(
        "BV1HATE",
        delight_score=0.93,
        delight_reason="disliked reason",
        delight_hook="disliked hook",
    )
    database.conn.execute(
        "UPDATE content_cache SET feedback_type = 'like' WHERE bvid = ?",
        ("BV1LIKE",),
    )
    database.conn.execute(
        "UPDATE content_cache SET feedback_type = 'dislike' WHERE bvid = ?",
        ("BV1HATE",),
    )
    database.conn.commit()

    candidates = database.get_delight_candidates(min_delight_score=0.85)

    assert [row["bvid"] for row in candidates] == ["BV1FRESH"]
    assert database.count_delight_candidates(min_delight_score=0.85) == 1


def test_delight_claim_threshold_floor_in_sync() -> None:
    """storage mirrors DEFAULT_DELIGHT_THRESHOLD as the dynamic floor.

    Storage stays leaf-only and receives profile-aware threshold floors
    from callers where available, so this constant only locks the
    default fallback floor.
    """
    from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD
    from openbiliclaw.storage.database import _DELIGHT_CLAIM_MIN_SCORE

    assert _DELIGHT_CLAIM_MIN_SCORE == DEFAULT_DELIGHT_THRESHOLD


def _seed_delight_scored_pool(
    database: Database,
    count: int,
    *,
    relevance_score: Callable[[int], float],
    delight_score: Callable[[int], float],
    prefix: str,
) -> None:
    for index in range(count):
        bvid = f"BV1{prefix}{index:04d}"
        database.cache_content(
            bvid,
            title=f"{prefix} {index}",
            relevance_score=relevance_score(index),
        )
        database.update_delight_score(
            bvid,
            delight_score=delight_score(index),
            delight_reason="",
            delight_hook="",
        )


def test_database_delight_candidates_include_liked_keeps_liked_rows(tmp_path: Path) -> None:
    """Queue re-hydration must keep liked delights visible (v0.3.63 contract).

    ``include_liked=True`` is what /api/delight/pending-batch passes so a
    liked card survives popup reopen; disliked rows stay excluded either way.
    """
    database = _make_database(tmp_path)
    database.cache_content("BV1LIKE", title="已喜欢", relevance_score=0.9)
    database.cache_content("BV1FRESH", title="新惊喜", relevance_score=0.9)
    database.cache_content("BV1HATE", title="已点不感兴趣", relevance_score=0.9)
    database.update_delight_score(
        "BV1LIKE",
        delight_score=0.95,
        delight_reason="liked reason",
        delight_hook="liked hook",
    )
    database.update_delight_score(
        "BV1FRESH",
        delight_score=0.94,
        delight_reason="fresh reason",
        delight_hook="fresh hook",
    )
    database.update_delight_score(
        "BV1HATE",
        delight_score=0.93,
        delight_reason="disliked reason",
        delight_hook="disliked hook",
    )
    database.conn.execute(
        "UPDATE content_cache SET feedback_type = 'like' WHERE bvid = ?",
        ("BV1LIKE",),
    )
    database.conn.execute(
        "UPDATE content_cache SET feedback_type = 'dislike' WHERE bvid = ?",
        ("BV1HATE",),
    )
    database.conn.commit()

    candidates = database.get_delight_candidates(min_delight_score=0.85, include_liked=True)

    assert [row["bvid"] for row in candidates] == ["BV1LIKE", "BV1FRESH"]

    # Explicit dismissal still removes a liked delight from re-hydration.
    database.mark_delight_notified("BV1LIKE")
    candidates = database.get_delight_candidates(min_delight_score=0.85, include_liked=True)
    assert [row["bvid"] for row in candidates] == ["BV1FRESH"]


def test_database_count_delight_candidates(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1A", title="A", relevance_score=0.9)
    database.cache_content("BV1B", title="B", relevance_score=0.8)
    database.update_delight_score(
        "BV1A",
        delight_score=0.92,
        delight_reason="r1",
        delight_hook="h1",
    )
    database.update_delight_score(
        "BV1B",
        delight_score=0.88,
        delight_reason="r2",
        delight_hook="h2",
    )

    count = database.count_delight_candidates(min_delight_score=0.85)
    assert count == 2

    database.mark_delight_notified("BV1A")
    count = database.count_delight_candidates(min_delight_score=0.85)
    assert count == 1


def test_database_dynamic_delight_threshold_keeps_floor_before_min_sample_size(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        149,
        relevance_score=lambda index: 0.91 + (index * 0.0001),
        delight_score=lambda index: 0.91 + (index * 0.0001),
        prefix="SMPL",
    )

    threshold = database.dynamic_delight_threshold(default_threshold=0.75)

    assert threshold == pytest.approx(0.75)


def test_database_dynamic_delight_threshold_keeps_floor_for_homogeneous_pool(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        160,
        relevance_score=lambda index: 0.91 + (index * 0.0001),
        delight_score=lambda index: 0.91 + (index * 0.0001),
        prefix="HOMO",
    )

    assert database.dynamic_delight_threshold(default_threshold=0.75) == pytest.approx(0.75)


def test_database_dynamic_delight_threshold_uses_delight_top_ten_percent_boundary(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        160,
        relevance_score=lambda index: 0.30 + (index * 0.004),
        delight_score=lambda index: 0.30 + (index * 0.004),
        prefix="DYNB",
    )

    threshold = database.dynamic_delight_threshold(default_threshold=0.75)

    assert threshold == pytest.approx(0.876)


def test_database_dynamic_delight_threshold_uses_delight_score_not_relevance_score(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        160,
        relevance_score=lambda index: 0.60 + (index * 0.001),
        delight_score=lambda index: 0.35 + (index * 0.004),
        prefix="DSRC",
    )

    threshold = database.dynamic_delight_threshold(default_threshold=0.75)

    assert threshold == pytest.approx(0.926)


def test_database_dynamic_delight_threshold_never_drops_below_default(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        160,
        relevance_score=lambda index: 0.01 + (index * 0.003),
        delight_score=lambda index: 0.01 + (index * 0.003),
        prefix="LOWB",
    )

    assert database.dynamic_delight_threshold(default_threshold=0.75) == pytest.approx(0.75)


def test_database_dynamic_delight_threshold_bad_default_uses_claim_floor(
    tmp_path: Path,
) -> None:
    from openbiliclaw.storage.database import _DELIGHT_CLAIM_MIN_SCORE

    database = _make_database(tmp_path)

    assert database.dynamic_delight_threshold(default_threshold="bad") == pytest.approx(
        _DELIGHT_CLAIM_MIN_SCORE
    )


def test_pool_candidates_use_dynamic_delight_claim_threshold(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    for index in range(40):
        score = 0.50 + (index * 0.01)
        bvid = f"BV1BASE{index:02d}"
        database.cache_content(bvid, title=bvid, relevance_score=score)
        database.conn.execute(
            """
            UPDATE content_cache
            SET pool_expression = 'copy',
                pool_topic_label = 'topic',
                style_key = 'deep_focus',
                topic_group = 'base'
            WHERE bvid = ?
            """,
            (bvid,),
        )

    database.cache_content("BV1MID", title="mid delight", relevance_score=0.72)
    database.conn.execute(
        """
        UPDATE content_cache
        SET pool_expression = 'copy',
            pool_topic_label = 'topic',
            style_key = 'deep_focus',
            topic_group = 'mid'
        WHERE bvid = 'BV1MID'
        """
    )
    database.update_delight_score(
        "BV1MID",
        delight_score=0.72,
        delight_reason="ready",
        delight_hook="hook",
    )
    database.conn.commit()

    rows = database.get_pool_candidates(limit=50, max_per_topic_group=0)

    assert "BV1MID" in [row["bvid"] for row in rows]


def test_database_get_pool_candidates_needing_delight_score(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    # Unscored item (delight_score = 0.0 default)
    database.cache_content("BV1UNSCORE", title="Unscored", relevance_score=0.8)
    # Already scored item
    database.cache_content("BV1SCORED", title="Scored", relevance_score=0.7)
    database.update_delight_score(
        "BV1SCORED",
        delight_score=0.5,
        delight_reason="",
        delight_hook="",
    )

    candidates = database.get_pool_candidates_needing_delight_score(limit=10)

    bvids = [c["bvid"] for c in candidates]
    assert "BV1UNSCORE" in bvids
    assert "BV1SCORED" not in bvids


def test_database_get_pool_candidates_needing_delight_score_includes_high_score_backfill(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1READY", title="Ready", relevance_score=0.9)
    database.update_delight_score(
        "BV1READY",
        delight_score=0.77,
        delight_reason="已经有解释",
        delight_hook="已完成",
    )
    database.cache_content("BV1BACKFILL", title="Backfill", relevance_score=0.88)
    database.update_delight_score(
        "BV1BACKFILL",
        delight_score=0.76,
        delight_reason="",
        delight_hook="",
    )
    database.conn.execute(
        "UPDATE content_cache SET pool_status = 'suppressed' WHERE bvid = ?",
        ("BV1BACKFILL",),
    )
    database.conn.commit()
    database.cache_content("BV1LOW", title="Low", relevance_score=0.7)
    database.update_delight_score(
        "BV1LOW",
        delight_score=0.55,
        delight_reason="",
        delight_hook="",
    )

    candidates = database.get_pool_candidates_needing_delight_score(
        limit=10,
        min_delight_score_for_reason=0.75,
    )

    bvids = [c["bvid"] for c in candidates]
    assert "BV1BACKFILL" in bvids
    assert "BV1READY" in bvids
    assert "BV1LOW" in bvids


def test_database_get_pool_candidates_needing_delight_score_includes_stale_score_backfill(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1STALE", title="Stale", relevance_score=0.92)
    database.update_delight_score(
        "BV1STALE",
        delight_score=0.72,
        delight_reason="旧阈值留下的理由",
        delight_hook="旧钩子",
    )

    candidates = database.get_pool_candidates_needing_delight_score(
        limit=10,
        min_delight_score_for_reason=0.91,
    )

    assert "BV1STALE" in [c["bvid"] for c in candidates]


def test_database_get_pool_candidates_needing_delight_score_prioritizes_current_relevance(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1LOWOLD", title="Old high score", relevance_score=0.60)
    database.update_delight_score(
        "BV1LOWOLD",
        delight_score=0.99,
        delight_reason="旧高分",
        delight_hook="旧",
    )
    database.cache_content("BV1HIGHNEW", title="Current high score", relevance_score=0.92)
    database.update_delight_score(
        "BV1HIGHNEW",
        delight_score=0.72,
        delight_reason="旧低分",
        delight_hook="旧",
    )

    candidates = database.get_pool_candidates_needing_delight_score(
        limit=1,
        min_delight_score_for_reason=0.91,
    )

    assert [c["bvid"] for c in candidates] == ["BV1HIGHNEW"]


def test_database_get_pool_candidates_needing_delight_score_includes_shown_stale_rows(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1SHOWN", title="Shown stale", relevance_score=0.92)
    database.update_delight_score(
        "BV1SHOWN",
        delight_score=0.72,
        delight_reason="旧低分",
        delight_hook="旧",
    )
    database.conn.execute(
        "UPDATE content_cache SET pool_status = 'shown' WHERE bvid = ?",
        ("BV1SHOWN",),
    )
    database.conn.commit()

    candidates = database.get_pool_candidates_needing_delight_score(
        limit=10,
        min_delight_score_for_reason=0.91,
    )

    assert "BV1SHOWN" in [c["bvid"] for c in candidates]


def test_database_get_pool_candidates_needing_delight_score_includes_shown_history_rows(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1HISTORY", title="Shown history stale", relevance_score=0.92)
    database.update_delight_score(
        "BV1HISTORY",
        delight_score=0.72,
        delight_reason="旧低分",
        delight_hook="旧",
    )
    database.conn.execute(
        "UPDATE content_cache SET pool_status = 'shown' WHERE bvid = ?",
        ("BV1HISTORY",),
    )
    database.insert_recommendation(
        "BV1HISTORY",
        confidence=0.92,
        expression="普通推荐历史",
        topic="普通推荐",
        presented=1,
    )
    database.conn.commit()

    candidates = database.get_pool_candidates_needing_delight_score(
        limit=10,
        min_delight_score_for_reason=0.91,
    )

    assert "BV1HISTORY" in [c["bvid"] for c in candidates]


# ---------------------------------------------------------------------------
# Delight owns the threshold policy only — no scorer of its own
# ---------------------------------------------------------------------------


def test_delight_module_no_longer_exposes_llm_score_compat_api() -> None:
    import openbiliclaw.recommendation.delight as delight

    assert not hasattr(delight, "LLMDelightScorer")
    assert not hasattr(delight, "DelightLLMResult")
    assert not hasattr(delight, "_extract_delight_entries")


def test_delight_module_exposes_threshold_policy_only() -> None:
    """v0.3.174+: the standalone embedding scorer is gone for good.

    ``delight_score`` is produced by ``precompute_delight_scores()`` reusing
    Evo's ``relevance_score``; catalogue signals reach the score through the
    shared evaluator prompt. A parallel weighted scorer here drifted out of
    the pipeline and sat unreferenced — don't resurrect it.
    """
    import openbiliclaw.recommendation.delight as delight

    for removed in (
        "DelightScorer",
        "DelightSignals",
        "DelightWeights",
        "SupportsDelightCandidate",
        "SupportsRecommendationSignalStore",
    ):
        assert not hasattr(delight, removed), f"{removed} was removed as dead code"

    # The threshold policy is the module's whole public surface.
    assert callable(delight.effective_delight_threshold)
    assert isinstance(delight.DEFAULT_DELIGHT_THRESHOLD, float)
    assert isinstance(delight.CONSERVATIVE_DELIGHT_THRESHOLD, float)


def test_get_pool_candidates_filters_by_min_relevance(tmp_path: Path) -> None:
    """relevance_score gate cuts weak-fit items before delight backfill."""
    database = _make_database(tmp_path)
    database.cache_content("BV1HIGH", title="High fit", relevance_score=0.85)
    database.cache_content("BV1MED", title="Moderate", relevance_score=0.60)
    database.cache_content("BV1LOW", title="Weak", relevance_score=0.40)

    rows = database.get_pool_candidates_needing_delight_score(
        limit=10,
        min_relevance_score=0.55,
    )
    bvids = {r["bvid"] for r in rows}
    assert "BV1HIGH" in bvids
    assert "BV1MED" in bvids
    assert "BV1LOW" not in bvids


def test_get_pool_candidates_default_min_relevance_is_055(tmp_path: Path) -> None:
    """v0.3.35: default gate must remain 0.55 (any change is a behaviour
    swing affecting how many candidates are checked per cycle)."""
    database = _make_database(tmp_path)
    database.cache_content("BV1HALF", title="Right at edge", relevance_score=0.54)
    database.cache_content("BV1OVER", title="Just over", relevance_score=0.56)

    # No min_relevance_score passed — uses default
    rows = database.get_pool_candidates_needing_delight_score(limit=10)
    bvids = {r["bvid"] for r in rows}
    assert "BV1OVER" in bvids
    assert "BV1HALF" not in bvids


def test_delight_serving_uses_exact_explore_relaxed_admission_floor(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    for bvid, source in (("BVEXP", "explore"), ("BVTREND", "trending")):
        database.cache_content(bvid, title=bvid, source=source, relevance_score=0.58)
        database.update_delight_score(
            bvid,
            delight_score=0.90,
            delight_reason="值得看看",
            delight_hook="新方向",
        )

    assert [
        row["bvid"] for row in database.get_delight_candidates(min_delight_score=0.75, limit=10)
    ] == ["BVEXP"]
