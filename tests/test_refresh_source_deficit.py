"""Pool-share fairness: production-side deficit uses own-share口径.

Spec: docs/plans/2026-07-20-pool-share-fairness-spec.md (Phase 1, invariant 2).

Before this fix ``_source_requested_count`` clamped every source's deficit by
the *global* available headroom, so once the global pool hit ``pool_target``
any under-share source reported deficit 0 and its producer never ran — even
when that source sat far below its own configured share. These tests pin the
new口径: ``available(s) < target(s)`` ⇒ deficit > 0, bounded only by raw
headroom, regardless of the global pool being full.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.storage.database import Database
from tests.test_refresh_runtime import (
    _FakeDatabase,
    _FakeDiscoveryEngine,
    _FakeMemoryManager,
    _FakeRecommendationEngine,
    _FakeSoulEngine,
)

if TYPE_CHECKING:
    from pathlib import Path


class _FakeBangumiProducer:
    def __init__(self) -> None:
        self.calls: list[int | None] = []

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        self.calls.append(limit)
        return {"discovered": 3, "reason": "ok"}


def _controller(**kwargs: object) -> ContinuousRefreshController:
    base: dict[str, object] = {
        "memory_manager": _FakeMemoryManager(),
        "soul_engine": _FakeSoulEngine(),
        "discovery_engine": _FakeDiscoveryEngine(),
        "recommendation_engine": _FakeRecommendationEngine(),
    }
    base.update(kwargs)
    return ContinuousRefreshController(**base)  # type: ignore[arg-type]


def test_under_share_source_has_deficit_even_when_global_pool_is_full() -> None:
    # 全局 available=300 (bilibili:288, bangumi:12), target 300, shares 5:1.
    # bangumi own target = 50 → deficit 50-12 = 38, NOT clamped to global 0.
    controller = _controller(
        database=_FakeDatabase(
            [],
            pool_count=300,
            source_available_counts={"bilibili": 288, "bangumi": 12},
            source_raw_counts={"bilibili": 288, "bangumi": 12},
        ),
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
    )

    assert controller._source_deficit("bangumi") == 38


async def test_bangumi_producer_runs_when_under_share_and_global_pool_full() -> None:
    producer = _FakeBangumiProducer()
    controller = _controller(
        database=_FakeDatabase(
            [],
            pool_count=300,
            source_available_counts={"bilibili": 288, "bangumi": 12},
            source_raw_counts={"bilibili": 288, "bangumi": 12},
        ),
        bangumi_producer=producer,
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
        discovery_limit=30,
    )

    await controller._tick_bangumi_producer()

    assert producer.calls == [30]


def test_source_at_or_above_share_keeps_zero_deficit() -> None:
    # bangumi at its own share (50/50) → deficit 0 even though global is short.
    controller = _controller(
        database=_FakeDatabase(
            [],
            pool_count=250,
            source_available_counts={"bilibili": 200, "bangumi": 50},
            source_raw_counts={"bilibili": 200, "bangumi": 50},
        ),
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
    )

    assert controller._source_deficit("bangumi") == 0


def test_deficit_is_clamped_by_raw_headroom() -> None:
    # bangumi wants 38 available rows but raw headroom is only 5 → deficit 5.
    controller = _controller(
        database=_FakeDatabase(
            [],
            pool_count=300,
            source_available_counts={"bilibili": 288, "bangumi": 12},
            source_raw_counts={"bilibili": 288, "bangumi": 95},
        ),
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
    )

    # raw ceiling = max(600, 420) = 600; bangumi raw target = 100; raw already 95
    # → raw_headroom 5 caps the 38-row available deficit.
    assert controller._source_deficit("bangumi") == 5


# ── Phase 3: gentle pool-share rebalance (evict over-share to seat under-share) ──


class _FakeRebalanceDB:
    def __init__(
        self,
        *,
        pool_count: int,
        available_by_family: dict[str, int],
        evaluated_by_family: dict[str, int],
    ) -> None:
        self.pool_count = pool_count
        self.available_by_family = dict(available_by_family)
        self.evaluated_by_family = dict(evaluated_by_family)
        self.demote_calls: list[tuple[str, int]] = []

    def count_pool_candidates(self, *, xhs_self_nickname: str = "") -> int:
        return self.pool_count

    def count_pool_available_candidates_by_source(
        self, *, max_per_topic_group: int = 3, xhs_self_nickname: str = ""
    ) -> dict[str, int]:
        return dict(self.available_by_family)

    def count_evaluated_discovery_candidates_by_source(self) -> dict[str, int]:
        return dict(self.evaluated_by_family)

    def demote_lowest_ranked_pool_rows(self, *, source_family: str, limit: int) -> int:
        self.demote_calls.append((source_family, limit))
        self.available_by_family[source_family] = max(
            0, self.available_by_family.get(source_family, 0) - limit
        )
        self.pool_count -= limit
        return limit


# shares {bilibili:8, reddit:1, bangumi:1} over 300 → targets 240 / 30 / 30.
_REBALANCE_SHARES = {"bilibili": 8, "reddit": 1, "bangumi": 1}


def test_rebalance_demotes_three_over_share_rows_for_waiting_under_share() -> None:
    # reddit 169/30 over-share, bangumi 0/30 under-share with 5 evaluated waiting,
    # global pool full → evict exactly 3 (the per-tick cap) reddit rows.
    db = _FakeRebalanceDB(
        pool_count=300,
        available_by_family={"reddit": 169, "bilibili": 131},
        evaluated_by_family={"bangumi": 5},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares=dict(_REBALANCE_SHARES),
    )

    demoted = controller._rebalance_pool_shares()

    assert demoted == 3
    assert db.demote_calls == [("reddit", 3)]


def test_rebalance_is_a_noop_without_under_share_waiting_supply() -> None:
    # reddit over-share but no under-share source has evaluated supply waiting.
    db = _FakeRebalanceDB(
        pool_count=300,
        available_by_family={"reddit": 169, "bilibili": 131},
        evaluated_by_family={},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares=dict(_REBALANCE_SHARES),
    )

    assert controller._rebalance_pool_shares() == 0
    assert db.demote_calls == []


def test_rebalance_caps_eviction_by_source_overage() -> None:
    # reddit is the only over-share family and its overage is just 2 (32/30) →
    # evict min(3, overage=2, waiting=9) = 2.
    db = _FakeRebalanceDB(
        pool_count=300,
        available_by_family={"reddit": 32, "bilibili": 200},
        evaluated_by_family={"bangumi": 9},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares=dict(_REBALANCE_SHARES),
    )

    demoted = controller._rebalance_pool_shares()

    assert demoted == 2
    assert db.demote_calls == [("reddit", 2)]


def test_rebalance_skipped_when_global_pool_below_target() -> None:
    db = _FakeRebalanceDB(
        pool_count=250,
        available_by_family={"reddit": 169, "bilibili": 81},
        evaluated_by_family={"bangumi": 5},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares=dict(_REBALANCE_SHARES),
    )

    assert controller._rebalance_pool_shares() == 0
    assert db.demote_calls == []


def test_demote_lowest_ranked_pool_rows_evicts_lowest_score_first(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    for bvid, score in (("BVhi", 0.9), ("BVmid", 0.6), ("BVlo", 0.3)):
        db.cache_content(
            bvid,
            title=bvid,
            up_name="UP",
            source="reddit",
            source_platform="reddit",
            relevance_score=score,
            relevance_reason="seed",
            pool_expression="推荐文案",
            pool_topic_label="推荐主题",
            style_key="deep_dive",
            topic_group="技术",
        )
    # A bilibili row must NOT be touched (different family).
    db.cache_content(
        "BVbili",
        title="BVbili",
        up_name="UP",
        source="search",
        source_platform="bilibili",
        relevance_score=0.1,
        pool_expression="x",
        pool_topic_label="x",
        style_key="deep_dive",
        topic_group="技术",
    )

    demoted = db.demote_lowest_ranked_pool_rows(source_family="reddit", limit=2)

    assert demoted == 2
    statuses = {
        row["bvid"]: row["pool_status"]
        for row in db.conn.execute(
            "SELECT bvid, COALESCE(pool_status, 'fresh') AS pool_status FROM content_cache"
        ).fetchall()
    }
    assert statuses["BVlo"] == "stale"
    assert statuses["BVmid"] == "stale"
    assert statuses["BVhi"] == "fresh"
    assert statuses["BVbili"] == "fresh"


# ── Phase 4: change-throttled per-source deficit summary logging ──


def test_source_deficit_summary_logs_once_per_change(caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    db = _FakeDatabase(
        [],
        pool_count=250,
        source_available_counts={"bilibili": 200, "bangumi": 50},
        source_raw_counts={"bilibili": 200, "bangumi": 50},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
    )

    with caplog.at_level(logging.INFO, logger="openbiliclaw.runtime.refresh"):
        controller._log_source_deficit_summary()
        controller._log_source_deficit_summary()  # unchanged → no second line

    summary_lines = [r for r in caplog.records if r.message.startswith("pool source shares:")]
    assert len(summary_lines) == 1

    # A change in the availability picture emits exactly one more line.
    db.source_available_counts = {"bilibili": 200, "bangumi": 40}
    with caplog.at_level(logging.INFO, logger="openbiliclaw.runtime.refresh"):
        controller._log_source_deficit_summary()

    summary_lines = [r for r in caplog.records if r.message.startswith("pool source shares:")]
    assert len(summary_lines) == 2


# ── Task 7: rebalance + summary reachable from the coordinator assembly ──


def test_run_pool_share_maintenance_invokes_rebalance_then_summary() -> None:
    # Both candidate-eval assemblies (legacy drain + CandidateEvalCoordinator)
    # funnel through this single controller entry point, so the Phase 3/4 hooks
    # are no longer dead code under the production (coordinator) wiring.
    controller = _controller(
        database=_FakeDatabase(
            [],
            pool_count=300,
            source_available_counts={"bilibili": 250, "bangumi": 50},
            source_raw_counts={"bilibili": 250, "bangumi": 50},
        ),
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
    )
    calls: list[str] = []
    controller._rebalance_pool_shares = lambda: (calls.append("rebalance"), 0)[1]  # type: ignore[method-assign]
    controller._log_source_deficit_summary = lambda: calls.append("summary")  # type: ignore[method-assign]

    controller.run_pool_share_maintenance()

    assert calls == ["rebalance", "summary"]
