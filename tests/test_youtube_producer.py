from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.runtime.youtube_producer import (
    YoutubeDiscoveryProducer,
    YoutubeStrategyRunResult,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "yt-producer.db")
    database.initialize()
    return database


class _Soul:
    async def get_profile(self) -> dict[str, object]:
        return {"profile": "ok"}


class _EmptySoul:
    async def get_profile(self) -> None:
        return None


class _RaisingSoul:
    async def get_profile(self) -> None:
        raise RuntimeError("profile unavailable")


@dataclass
class _Discover:
    calls: list[tuple[str, int, int]]

    async def __call__(
        self,
        profile: Any,
        *,
        strategy: str,
        unit_budget: int,
        result_limit: int,
    ) -> YoutubeStrategyRunResult:
        assert profile == {"profile": "ok"}
        self.calls.append((strategy, unit_budget, result_limit))
        return YoutubeStrategyRunResult(
            items=[object()] * min(2, result_limit),
            units_used=unit_budget,
            source_counts={strategy: min(2, result_limit)},
        )


@dataclass
class _SometimesFailingDiscover:
    fail: set[str]
    calls: list[str] = field(default_factory=list)

    async def __call__(
        self,
        profile: Any,
        *,
        strategy: str,
        unit_budget: int,
        result_limit: int,
    ) -> YoutubeStrategyRunResult:
        self.calls.append(strategy)
        if strategy in self.fail:
            raise RuntimeError(f"{strategy} failed")
        return YoutubeStrategyRunResult(
            items=[object()],
            units_used=unit_budget,
            source_counts={strategy: 1},
        )


class _FakeCandidatePipeline:
    def __init__(self, *, pool_full: bool = False) -> None:
        self._pool_full = pool_full
        self.enqueued: list[tuple[list[object], str]] = []
        self.drains: list[int] = []

    def pool_full(self) -> bool:
        return self._pool_full

    def enqueue_candidates(self, items: list[object], *, source_context: str = "") -> int:
        self.enqueued.append((list(items), source_context))
        return len(items)

    async def drain_pending(self, *, profile: object, batch_size: int = 30) -> dict[str, int]:
        self.drains.append(batch_size)
        return {"evaluated": batch_size, "cached": 2, "rejected": 0}


async def test_youtube_producer_produces_when_due(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        daily_search_budget=3,
        daily_trending_budget=5,
        daily_channel_budget=2,
    )

    result = await producer.produce_if_due(limit=4)

    assert result == {
        "discovered": 6,
        "source_counts": {"yt_search": 2, "yt_trending": 2, "yt_channel": 2},
        "reason": "ok",
    }
    assert discover.calls == [
        ("yt_search", 3, 4),
        ("yt_trending", 5, 4),
        ("yt_channel", 2, 4),
    ]
    assert producer.consumed_today("yt_search") == 3
    assert producer.consumed_today("yt_trending") == 5
    assert producer.consumed_today("yt_channel") == 2


async def test_youtube_producer_enqueues_raw_candidates_when_pipeline_is_available(
    db: Database,
) -> None:
    discover = _Discover([])
    pipeline = _FakeCandidatePipeline()
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        daily_search_budget=2,
        strategies=("yt_search",),
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=4)

    assert discover.calls == [("yt_search", 2, 4)]
    assert len(pipeline.enqueued) == 1
    assert pipeline.enqueued[0][1] == "yt_search"
    assert len(pipeline.enqueued[0][0]) == 2
    assert pipeline.drains == [4]
    assert result["discovered"] == 2
    assert result["enqueued"] == 2
    assert result["cached"] == 2


async def test_youtube_producer_stamps_strategy_score_threshold_before_enqueue(
    db: Database,
) -> None:
    pipeline = _FakeCandidatePipeline()

    async def discover(
        profile: Any,
        *,
        strategy: str,
        unit_budget: int,
        result_limit: int,
    ) -> YoutubeStrategyRunResult:
        return YoutubeStrategyRunResult(
            items=[
                DiscoveredContent(
                    content_id="yt-channel-1",
                    title="Channel",
                    source_platform="youtube",
                    source_strategy=strategy,
                )
            ],
            units_used=unit_budget,
            source_counts={strategy: 1},
        )

    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        daily_channel_budget=1,
        strategies=("yt_channel",),
        candidate_pipeline=pipeline,
    )

    await producer.produce_if_due(limit=4)

    assert pipeline.enqueued
    assert pipeline.enqueued[0][0][0].score_threshold == 0.65


async def test_youtube_producer_skips_discovery_when_pipeline_pool_is_full(
    db: Database,
) -> None:
    discover = _Discover([])
    pipeline = _FakeCandidatePipeline(pool_full=True)
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        daily_search_budget=3,
        daily_trending_budget=5,
        daily_channel_budget=2,
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=4)

    assert result["reason"] == "pool_full"
    assert discover.calls == []
    assert pipeline.enqueued == []
    assert pipeline.drains == []


async def test_youtube_producer_throttles_recent_run(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=60,
    )
    producer._last_run_at = datetime.now(UTC) - timedelta(minutes=5)

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "throttled"}
    assert discover.calls == []


async def test_youtube_producer_skips_when_daily_budget_exhausted(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=0,
        daily_search_budget=1,
        daily_trending_budget=1,
        daily_channel_budget=1,
    )
    producer.record_strategy_run("yt_search", units_used=1, discovered=0, reason="ok")
    producer.record_strategy_run("yt_trending", units_used=1, discovered=0, reason="ok")
    producer.record_strategy_run("yt_channel", units_used=1, discovered=0, reason="ok")

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "budget_exhausted"}
    assert discover.calls == []


async def test_youtube_producer_zero_daily_budget_uses_per_run_limit(
    db: Database,
) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=0,
        daily_search_budget=0,
        daily_trending_budget=0,
        daily_channel_budget=0,
    )
    producer.record_strategy_run("yt_search", units_used=999, discovered=0, reason="ok")
    producer.record_strategy_run("yt_trending", units_used=999, discovered=0, reason="ok")
    producer.record_strategy_run("yt_channel", units_used=999, discovered=0, reason="ok")

    result = await producer.produce_if_due(limit=5)

    assert result["reason"] == "ok"
    assert discover.calls == [
        ("yt_search", 5, 5),
        ("yt_trending", 5, 5),
        ("yt_channel", 5, 5),
    ]


async def test_youtube_producer_skips_when_disabled(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        enabled=False,
    )

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "disabled"}
    assert discover.calls == []


async def test_youtube_producer_skips_without_profile(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_EmptySoul(),
        discover=discover,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "no_profile"}
    assert discover.calls == []


async def test_youtube_producer_skips_when_profile_raises(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_RaisingSoul(),
        discover=discover,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "no_profile"}
    assert discover.calls == []


async def test_youtube_producer_min_interval_zero_is_always_due(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=0,
        daily_search_budget=1,
        strategies=("yt_search",),
    )
    producer._last_run_at = datetime.now(UTC)

    result = await producer.produce_if_due(limit=5)

    assert result["reason"] == "ok"
    assert discover.calls == [("yt_search", 1, 5)]


async def test_youtube_producer_runs_only_strategies_with_remaining_budget(
    db: Database,
) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=0,
        daily_search_budget=2,
        daily_trending_budget=4,
        daily_channel_budget=1,
    )
    producer.record_strategy_run("yt_search", units_used=2, discovered=0, reason="ok")
    producer.record_strategy_run("yt_channel", units_used=1, discovered=0, reason="ok")

    result = await producer.produce_if_due(limit=3)

    assert result["reason"] == "ok"
    assert discover.calls == [("yt_trending", 4, 3)]


async def test_youtube_producer_returns_ok_when_one_strategy_fails(
    db: Database,
) -> None:
    discover = _SometimesFailingDiscover(fail={"yt_search"})
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=0,
        daily_search_budget=1,
        daily_trending_budget=1,
        strategies=("yt_search", "yt_trending"),
    )

    result = await producer.produce_if_due(limit=3)

    assert result == {
        "discovered": 1,
        "source_counts": {"yt_trending": 1},
        "reason": "ok",
    }
    assert discover.calls == ["yt_search", "yt_trending"]
    assert producer.consumed_today("yt_search") == 0
    assert producer.consumed_today("yt_trending") == 1


async def test_youtube_producer_returns_error_when_all_strategies_fail(
    db: Database,
) -> None:
    discover = _SometimesFailingDiscover(fail={"yt_search", "yt_trending"})
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=0,
        daily_search_budget=1,
        daily_trending_budget=1,
        strategies=("yt_search", "yt_trending"),
    )

    result = await producer.produce_if_due(limit=3)

    assert result == {"discovered": 0, "reason": "error"}
    assert discover.calls == ["yt_search", "yt_trending"]
