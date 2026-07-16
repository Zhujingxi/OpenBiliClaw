from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openbiliclaw.features.sources.domain import SourceOperation
from openbiliclaw.infrastructure.sources.bilibili import build_bilibili_connector
from openbiliclaw.infrastructure.sources.douyin import (
    DouyinDirectTransport,
    build_douyin_connector,
)
from openbiliclaw.infrastructure.sources.reddit import build_reddit_connector
from openbiliclaw.infrastructure.sources.twitter import build_twitter_connector
from openbiliclaw.infrastructure.sources.xiaohongshu import build_xiaohongshu_connector
from openbiliclaw.infrastructure.sources.youtube import (
    YouTubeDirectTransport,
    build_youtube_connector,
)
from openbiliclaw.infrastructure.sources.zhihu import build_zhihu_connector


class BilibiliClient:
    async def search(
        self, keyword: str, *, page: int = 1, page_size: int = 20, order: str = "totalrank"
    ) -> list[dict[str, Any]]:
        return [{"bvid": "search", "title": keyword}]

    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]:
        return [{"bvid": "history", "event_type": "view"}]

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
        max_total_items: int | None = None,
    ) -> list[Any]:
        return [SimpleNamespace(items=[{"bvid": "favorite"}])]

    async def get_following(self, *, page: int = 1, page_size: int = 50) -> list[Any]:
        return [SimpleNamespace(mid=1, uname="creator")]

    async def get_related_videos(self, bvid: str) -> list[dict[str, Any]]:
        return [{"bvid": "related", "title": bvid}]

    async def get_ranking(self, rid: int = 0) -> list[dict[str, Any]]:
        return [{"bvid": "ranking", "title": "ranking"}]


class TwitterClient:
    async def search(self, query: str, *, limit: int, product: str = "Top") -> list[dict[str, Any]]:
        return [{"id": "search", "text": query}]

    async def for_you(self, *, limit: int) -> list[dict[str, Any]]:
        return [{"id": "feed", "text": "feed"}]

    async def user_tweets(self, handle: str, *, limit: int) -> list[dict[str, Any]]:
        return [{"id": "creator", "text": handle}]

    async def likes(self, *, limit: int) -> list[dict[str, Any]]:
        return [{"id": "liked", "text": "liked"}]

    async def bookmarks(self, *, limit: int) -> list[dict[str, Any]]:
        return [{"id": "saved", "text": "saved"}]


async def test_production_builders_wrap_retained_bilibili_and_x_clients() -> None:
    bilibili = build_bilibili_connector(BilibiliClient())
    twitter = build_twitter_connector(TwitterClient())
    assert (await bilibili.execute(SourceOperation.SEARCH, "python", 3))[0].external_id == "search"
    assert {
        event.kind.value for event in await bilibili.execute(SourceOperation.BOOTSTRAP_IMPORT)
    } == {
        "view",
        "favorite",
        "follow",
    }
    assert len(await twitter.execute(SourceOperation.BOOTSTRAP_IMPORT, limit=3)) == 2


class DouyinClient:
    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, Any]]:
        return [{"aweme_id": "search"}]

    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return [{"aweme_id": "hot"}]

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return [{"aweme_id": "feed"}]


class YouTubeClient:
    async def search_videos(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return [{"videoId": "search"}]

    async def get_trending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [{"videoId": "hot"}]

    async def get_channel_videos(self, channel_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return [{"videoId": "creator"}]


async def test_direct_transports_call_retained_douyin_and_youtube_clients() -> None:
    assert (
        await DouyinDirectTransport(DouyinClient()).fetch(operation="feed", query=None, limit=3)
    )[0]["aweme_id"] == "feed"
    assert (
        await YouTubeDirectTransport(YouTubeClient()).fetch(
            operation="creator", query="UC1", limit=3
        )
    )[0]["videoId"] == "creator"


def test_every_source_has_a_production_composition_builder() -> None:
    task_service = object()
    connectors = (
        build_bilibili_connector(BilibiliClient()),
        build_xiaohongshu_connector(task_service),
        build_douyin_connector(task_service=task_service, direct_client=DouyinClient()),
        build_youtube_connector(YouTubeClient(), task_service),
        build_twitter_connector(TwitterClient()),
        build_zhihu_connector(task_service),
        build_reddit_connector(task_service=task_service),
    )
    assert tuple(connector.manifest.source_id.value for connector in connectors) == (
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
        "reddit",
    )
