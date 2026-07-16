from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from openbiliclaw.bilibili.api import BilibiliAPIError
from openbiliclaw.features.sources.domain import (
    SourceOperation,
    SourceResultKind,
    SourceTransportKind,
)
from openbiliclaw.features.sources.registry import build_source_registry
from openbiliclaw.features.sources.service import SourceTaskService
from openbiliclaw.infrastructure.database.base import DatabaseSettings, create_engine_and_session
from openbiliclaw.infrastructure.database.models import SourceTaskModel
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.sources.bilibili import build_bilibili_connector
from openbiliclaw.infrastructure.sources.douyin import (
    DouyinDirectTransport,
    DouyinSettings,
    build_douyin_connector,
)
from openbiliclaw.infrastructure.sources.reddit import (
    RedditCliTransport,
    RedditSettings,
    build_reddit_connector,
)
from openbiliclaw.infrastructure.sources.twitter import build_twitter_connector
from openbiliclaw.infrastructure.sources.xiaohongshu import build_xiaohongshu_connector
from openbiliclaw.infrastructure.sources.youtube import (
    YouTubeDirectTransport,
    build_youtube_connector,
)
from openbiliclaw.infrastructure.sources.zhihu import build_zhihu_connector

from .test_browser_tasks import task_context  # noqa: F401
from .test_connector_contract import ACTIVITY_ROWS, CONTENT_ROWS

if TYPE_CHECKING:
    from pathlib import Path


class BilibiliClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search(
        self, keyword: str, *, page: int = 1, page_size: int = 20, order: str = "totalrank"
    ) -> list[dict[str, Any]]:
        self.calls.append("search")
        return [{"bvid": "search", "title": keyword}]

    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]:
        self.calls.append("history")
        return [{"bvid": "history", "event_type": "view"}]

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
        max_total_items: int | None = None,
    ) -> list[Any]:
        self.calls.append("favorites")
        return [SimpleNamespace(items=[{"bvid": "favorite"}])]

    async def get_following(self, *, page: int = 1, page_size: int = 50) -> list[Any]:
        self.calls.append("following")
        return [SimpleNamespace(mid=1, uname="creator")]

    async def get_related_videos(self, bvid: str) -> list[dict[str, Any]]:
        self.calls.append("related")
        return [{"bvid": "related", "title": bvid}]

    async def get_ranking(self, rid: int = 0) -> list[dict[str, Any]]:
        self.calls.append("ranking")
        return [{"bvid": "ranking", "title": "ranking"}]

    @classmethod
    def search_dom_fallback_remaining(cls) -> float:
        return 0.0

    @classmethod
    def search_cooldown_remaining(cls) -> float:
        return 0.0


class TwitterClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search(self, query: str, *, limit: int, product: str = "Top") -> list[dict[str, Any]]:
        self.calls.append("search")
        return [{"id": "search", "text": query}]

    async def for_you(self, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append("feed")
        return [{"id": "feed", "text": "feed"}]

    async def user_tweets(self, handle: str, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append("creator")
        return [{"id": "creator", "text": handle}]

    async def likes(self, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append("likes")
        return [{"id": "liked", "text": "liked"}]

    async def bookmarks(self, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append("bookmarks")
        return [{"id": "saved", "text": "saved"}]


async def test_production_builders_wrap_retained_bilibili_and_x_clients(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    _, _, service = task_context
    bilibili_client = BilibiliClient()
    twitter_client = TwitterClient()
    bilibili = build_bilibili_connector(bilibili_client, service)
    twitter = build_twitter_connector(twitter_client)
    assert (await bilibili.execute(SourceOperation.SEARCH, "python", 3))[0].external_id == "search"
    assert {
        event.kind.value for event in await bilibili.execute(SourceOperation.BOOTSTRAP_IMPORT)
    } == {
        "view",
        "favorite",
        "follow",
    }
    assert len(await twitter.execute(SourceOperation.BOOTSTRAP_IMPORT, limit=3)) == 2
    assert len(await twitter.execute(SourceOperation.BOOTSTRAP_IMPORT, limit=1)) == 1
    await bilibili.execute(SourceOperation.TRENDING, limit=3)
    await bilibili.execute(SourceOperation.RELATED, "BV1seed", 3)
    await twitter.execute(SourceOperation.SEARCH, "python", 3)
    await twitter.execute(SourceOperation.FEED, limit=3)
    await twitter.execute(SourceOperation.CREATOR, "author", 3)
    assert set(bilibili_client.calls) == {
        "search",
        "history",
        "favorites",
        "following",
        "ranking",
        "related",
    }
    assert set(twitter_client.calls) == {
        "likes",
        "bookmarks",
        "search",
        "feed",
        "creator",
    }


class DouyinClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, Any]]:
        self.calls.append("search")
        return [{"aweme_id": "search"}]

    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, Any]]:
        self.calls.append("hot")
        return [{"aweme_id": "hot"}]

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, Any]]:
        self.calls.append("feed")
        return [{"aweme_id": "feed"}]


class YouTubeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search_videos(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        self.calls.append("search")
        return [{"videoId": "search"}]

    async def get_trending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self.calls.append("trending")
        return [{"videoId": "hot"}]

    async def get_channel_videos(self, channel_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        self.calls.append("creator")
        return [{"videoId": "creator"}]


async def test_direct_transports_call_retained_douyin_and_youtube_clients() -> None:
    douyin_client = DouyinClient()
    youtube_client = YouTubeClient()
    douyin = DouyinDirectTransport(douyin_client)
    youtube = YouTubeDirectTransport(youtube_client)
    await douyin.fetch(operation="search", query="python", limit=3)
    await douyin.fetch(operation="trending", query=None, limit=3)
    await douyin.fetch(operation="feed", query=None, limit=3)
    await youtube.fetch(operation="search", query="python", limit=3)
    await youtube.fetch(operation="trending", query=None, limit=3)
    await youtube.fetch(operation="creator", query="UC1", limit=3)
    assert douyin_client.calls == ["search", "hot", "feed"]
    assert youtube_client.calls == ["search", "trending", "creator"]


async def test_reddit_cli_transport_maps_every_retained_operation() -> None:
    calls: list[list[str]] = []

    def runner(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='[{"name":"t3_result","title":"Result"}]',
            stderr="",
        )

    transport = RedditCliTransport(runner)
    await transport.fetch(operation="search", query="python", limit=3)
    await transport.fetch(operation="trending", query=None, limit=3)
    await transport.fetch(operation="community", query="python", limit=3)
    await transport.fetch(
        operation="related",
        query="https://www.reddit.com/r/python/comments/abc/example/",
        limit=3,
    )
    assert [call[1] for call in calls] == ["search", "all", "sub", "read"]


def test_every_source_has_a_production_composition_builder(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    _, _, task_service = task_context
    connectors = (
        build_bilibili_connector(BilibiliClient(), task_service),
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


@pytest.fixture
def production_context(tmp_path: Path):  # type: ignore[no-untyped-def]
    url = f"sqlite:///{tmp_path / 'production-sources.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=url))
    registry_holder: dict[str, Any] = {}
    service = SourceTaskService(
        lambda: UnitOfWork(session_factory),
        lambda: registry_holder["registry"],
    )
    registry = build_source_registry(
        bilibili=build_bilibili_connector(BilibiliClient(), service),
        xiaohongshu=build_xiaohongshu_connector(service),
        douyin=build_douyin_connector(
            task_service=service,
            settings=DouyinSettings(mode="extension"),
        ),
        youtube=build_youtube_connector(YouTubeClient(), service),
        twitter=build_twitter_connector(TwitterClient()),
        zhihu=build_zhihu_connector(service),
        reddit=build_reddit_connector(
            task_service=service,
            settings=RedditSettings(backend="extension"),
        ),
    )
    registry_holder["registry"] = registry
    yield session_factory, service, registry
    engine.dispose()


async def test_real_deferred_composition_executes_every_primary_browser_mapping(
    production_context: tuple[Any, Any, Any],
) -> None:
    _, service, registry = production_context
    for source_id in registry.source_ids:
        connector = registry.get(source_id)
        for spec in connector.manifest.operations:
            if spec.transport_kind is not SourceTransportKind.BROWSER:
                continue
            query = "seed" if spec.operation.requires_input else None
            pending = asyncio.create_task(connector.execute(spec.operation, query, 3))
            claim = None
            while claim is None:
                claim = await asyncio.to_thread(service.claim, source_id)
                await asyncio.sleep(0)
            row = (
                ACTIVITY_ROWS[source_id]
                if spec.result_kind is SourceResultKind.ACTIVITY
                else CONTENT_ROWS[source_id]
            )
            await asyncio.to_thread(
                service.complete,
                claim.id,
                claim.lease_token,
                {"items": [row]},
            )
            assert await pending


class ExpectedFallbackBilibiliClient(BilibiliClient):
    @classmethod
    def search_dom_fallback_remaining(cls) -> float:
        return 10.0

    async def search(
        self, keyword: str, *, page: int = 1, page_size: int = 20, order: str = "totalrank"
    ) -> list[dict[str, Any]]:
        raise BilibiliAPIError("expected transport block", code=-412)


async def test_bilibili_direct_success_does_not_enqueue_browser_fallback(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    connector = build_bilibili_connector(BilibiliClient(), service)
    result = await connector.execute(SourceOperation.SEARCH, "python", 3)
    assert result[0].external_id == "search"
    with session_factory() as session:
        assert session.scalar(select(SourceTaskModel)) is None


async def test_bilibili_expected_direct_failure_uses_browser_fallback(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    _, _, service = task_context
    connector = build_bilibili_connector(ExpectedFallbackBilibiliClient(), service)
    pending = asyncio.create_task(connector.execute(SourceOperation.SEARCH, "python", 3))
    claim = None
    while claim is None:
        claim = await asyncio.to_thread(service.claim, "bilibili")
        await asyncio.sleep(0)
    await asyncio.to_thread(
        service.complete,
        claim.id,
        claim.lease_token,
        {"items": [{"bvid": "browser", "title": "Browser fallback"}]},
    )
    assert (await pending)[0].external_id == "browser"


class ProgrammingErrorBilibiliClient(ExpectedFallbackBilibiliClient):
    async def search(
        self, keyword: str, *, page: int = 1, page_size: int = 20, order: str = "totalrank"
    ) -> list[dict[str, Any]]:
        raise TypeError("programming error")


async def test_bilibili_fallback_does_not_swallow_programming_errors(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    _, _, service = task_context
    connector = build_bilibili_connector(ProgrammingErrorBilibiliClient(), service)
    with pytest.raises(TypeError, match="programming error"):
        await connector.execute(SourceOperation.SEARCH, "python", 3)
