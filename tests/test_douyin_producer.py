from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from openbiliclaw.discovery.douyin import DouyinDiscoveryOptions, DouyinDiscoveryResult
from openbiliclaw.runtime.douyin_producer import (
    DouyinDiscoveryProducer,
    douyin_runtime_hot_budget,
)


class _FakeSoulEngine:
    async def get_profile(self) -> dict[str, object]:
        return {"profile": "ok"}


async def test_douyin_producer_invokes_discovery_with_cache_options() -> None:
    calls: list[tuple[dict[str, object], DouyinDiscoveryOptions]] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        calls.append((profile, options))
        return DouyinDiscoveryResult(
            items=[SimpleNamespace(), SimpleNamespace()],
            cached=True,
            source_counts={"dy-plugin-search": 2},
        )

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("search", "hot", "feed"),
    )

    result = await producer.produce_if_due(limit=12)

    assert result == {
        "discovered": 2,
        "cached": True,
        "source_counts": {"dy-plugin-search": 2},
        "reason": "ok",
    }
    assert len(calls) == 1
    profile, options = calls[0]
    assert profile == {"profile": "ok"}
    assert options.limit == 12
    assert options.sources == ("search", "hot")
    assert options.cache is True
    assert options.evaluate is True
    assert options.keywords_per_run == 1


async def test_douyin_producer_uses_feed_only_for_tiny_runtime_gap() -> None:
    calls: list[DouyinDiscoveryOptions] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        calls.append(options)
        return DouyinDiscoveryResult(items=[], cached=True, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("search", "hot", "feed"),
    )

    await producer.produce_if_due(limit=3)

    assert calls[0].sources == ("feed",)
    assert calls[0].per_source_limit == 3


async def test_douyin_producer_restores_search_for_larger_runtime_gap() -> None:
    calls: list[DouyinDiscoveryOptions] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        calls.append(options)
        return DouyinDiscoveryResult(items=[], cached=True, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("search", "hot", "feed"),
    )

    await producer.produce_if_due(limit=12)

    assert calls[0].sources == ("search", "hot")


async def test_douyin_producer_uses_hot_before_feed_for_medium_runtime_gap() -> None:
    calls: list[DouyinDiscoveryOptions] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        calls.append(options)
        return DouyinDiscoveryResult(items=[], cached=True, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("search", "hot", "feed"),
    )

    await producer.produce_if_due(limit=7)

    assert calls[0].sources == ("hot", "feed")


def test_douyin_runtime_hot_budget_scales_with_runtime_deficit() -> None:
    assert douyin_runtime_hot_budget(base_budget=5, requested_limit=30) == 30
    assert douyin_runtime_hot_budget(base_budget=40, requested_limit=30) == 40
    assert douyin_runtime_hot_budget(base_budget=5, requested_limit=3) == 5


def test_douyin_runtime_hot_budget_preserves_zero_as_no_daily_cap() -> None:
    assert douyin_runtime_hot_budget(base_budget=0, requested_limit=30) == 0


async def test_douyin_producer_throttles_recent_runs() -> None:
    calls = 0

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        nonlocal calls
        calls += 1
        return DouyinDiscoveryResult(items=[], cached=True, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=30,
    )
    producer._last_run_at = datetime.now(UTC) - timedelta(minutes=5)

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "throttled"}
    assert calls == 0


async def test_douyin_producer_soft_skips_when_profile_unavailable() -> None:
    class _BrokenSoulEngine:
        async def get_profile(self) -> object:
            raise RuntimeError("not ready")

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        raise AssertionError("should not discover without profile")

    producer = DouyinDiscoveryProducer(
        soul_engine=_BrokenSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "no_profile"}
