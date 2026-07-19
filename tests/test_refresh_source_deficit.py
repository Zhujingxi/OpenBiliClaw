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

from openbiliclaw.runtime.refresh import ContinuousRefreshController
from tests.test_refresh_runtime import (
    _FakeDatabase,
    _FakeDiscoveryEngine,
    _FakeMemoryManager,
    _FakeRecommendationEngine,
    _FakeSoulEngine,
)


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
