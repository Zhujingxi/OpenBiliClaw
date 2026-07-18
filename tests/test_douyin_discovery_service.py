"""Tests for the reusable Douyin discovery service."""

from __future__ import annotations

import pytest

from openbiliclaw.discovery.douyin import (
    DouyinDiscoveryOptions,
    DouyinDiscoveryService,
    split_csv_values,
)
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile


class _FakeClient:
    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
        return [{"aweme_id": "1", "desc": f"{keyword} 视频", "author": {"nickname": "A"}}]

    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
        return [{"aweme_id": "2", "desc": "热点视频", "author": {"nickname": "B"}}]

    async def get_creator_posts(self, sec_uid: str, *, limit: int = 30) -> list[dict[str, object]]:
        return [{"aweme_id": "3", "desc": f"{sec_uid} 作者视频", "author": {"nickname": "C"}}]

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, object]]:
        return [{"aweme_id": "4", "desc": "首页推荐视频", "author": {"nickname": "D"}}]


class _FakeDiscoveryEngine:
    def __init__(self) -> None:
        self.registered: list[object] = []
        self.calls: list[tuple[list[str] | None, int]] = []

    def register_strategy(self, strategy: object) -> None:
        self.registered.append(strategy)

    async def discover(
        self,
        profile: SoulProfile,
        strategies: list[str] | None = None,
        limit: int = 30,
    ) -> list[DiscoveredContent]:
        self.calls.append((strategies, limit))
        return [
            DiscoveredContent(
                bvid="dy:engine",
                title="engine item",
                source_platform="douyin",
                source_strategy="dy-direct-search",
                content_id="engine",
                content_url="https://www.douyin.com/video/engine",
            )
        ]


class _BackfillDiscoveryEngine:
    def register_strategy(self, strategy: object) -> None:
        del strategy

    async def discover(
        self,
        profile: SoulProfile,
        strategies: list[str] | None = None,
        limit: int = 30,
    ) -> list[DiscoveredContent]:
        del profile, strategies, limit
        return [
            DiscoveredContent(
                bvid="BV-backfill",
                title="bilibili backfill",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BV-backfill",
                content_url="https://www.bilibili.com/video/BV-backfill",
            )
        ]


def _profile() -> SoulProfile:
    return SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag(name="机械键盘", category="科技", weight=0.9)]
        )
    )


def test_split_csv_values_dedupes_and_preserves_order() -> None:
    assert split_csv_values([" a,b ", "b", " c "]) == ("a", "b", "c")


def test_douyin_discovery_defaults_expose_search_hot_feed() -> None:
    assert DouyinDiscoveryOptions().sources == ("search", "hot", "feed")


@pytest.mark.asyncio
async def test_service_uses_discovery_engine_when_cache_enabled() -> None:
    engine = _FakeDiscoveryEngine()
    service = DouyinDiscoveryService(client=_FakeClient(), discovery_engine=engine)

    result = await service.discover(
        _profile(),
        DouyinDiscoveryOptions(limit=7, keywords=("猫咪",), cache=True),
    )

    assert result.items[0].content_id == "engine"
    assert result.cached is True
    assert result.source_counts == {"dy-direct-search": 1}
    assert engine.calls == [(["douyin_direct"], 7)]
    assert engine.registered


@pytest.mark.asyncio
async def test_service_filters_engine_backfill_to_douyin_platform() -> None:
    service = DouyinDiscoveryService(
        client=_FakeClient(),
        discovery_engine=_BackfillDiscoveryEngine(),
    )

    result = await service.discover(
        _profile(),
        DouyinDiscoveryOptions(limit=5, sources=("hot",), cache=True),
    )

    assert result.items == []
    assert result.source_counts == {}


@pytest.mark.asyncio
async def test_service_can_run_without_cache_for_debug() -> None:
    service = DouyinDiscoveryService(client=_FakeClient(), discovery_engine=None)

    result = await service.discover(
        _profile(),
        DouyinDiscoveryOptions(
            limit=10,
            sources=("search", "hot", "feed"),
            keywords=("猫咪",),
            cache=False,
            evaluate=False,
        ),
    )

    assert result.cached is False
    assert result.source_counts == {
        "dy-direct-search": 1,
        "dy-direct-hot": 1,
        "dy-direct-feed": 1,
    }
    assert [item.content_id for item in result.items] == ["1", "2", "4"]
