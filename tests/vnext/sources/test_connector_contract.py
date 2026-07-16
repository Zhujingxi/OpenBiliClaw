"""Shared capability and normalization contracts for every retained source."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from openbiliclaw.features.activity.domain import ActivityEvent
from openbiliclaw.features.feed.domain import ContentItem
from openbiliclaw.features.sources.domain import (
    SourceCapability,
    UnsupportedSourceOperationError,
)
from openbiliclaw.features.sources.registry import build_source_registry
from openbiliclaw.infrastructure.sources.bilibili import BilibiliConnector, BilibiliSettings
from openbiliclaw.infrastructure.sources.douyin import DouyinConnector, DouyinSettings
from openbiliclaw.infrastructure.sources.reddit import RedditConnector, RedditSettings
from openbiliclaw.infrastructure.sources.twitter import TwitterConnector, TwitterSettings
from openbiliclaw.infrastructure.sources.xiaohongshu import (
    XiaohongshuConnector,
    XiaohongshuSettings,
)
from openbiliclaw.infrastructure.sources.youtube import YouTubeConnector, YouTubeSettings
from openbiliclaw.infrastructure.sources.zhihu import ZhihuConnector, ZhihuSettings

EXPECTED_CAPABILITIES = {
    "bilibili": {
        SourceCapability.ACTIVITY_IMPORT,
        SourceCapability.SEARCH,
        SourceCapability.TRENDING,
        SourceCapability.RELATED,
        SourceCapability.EXPLORE,
    },
    "xiaohongshu": {
        SourceCapability.ACTIVITY_IMPORT,
        SourceCapability.SEARCH,
        SourceCapability.CREATOR,
    },
    "douyin": {
        SourceCapability.ACTIVITY_IMPORT,
        SourceCapability.SEARCH,
        SourceCapability.TRENDING,
        SourceCapability.RECOMMENDED,
    },
    "youtube": {
        SourceCapability.ACTIVITY_IMPORT,
        SourceCapability.SEARCH,
        SourceCapability.TRENDING,
        SourceCapability.CREATOR,
    },
    "twitter": {
        SourceCapability.SEARCH,
        SourceCapability.RECOMMENDED,
        SourceCapability.CREATOR,
    },
    "zhihu": {
        SourceCapability.ACTIVITY_IMPORT,
        SourceCapability.SEARCH,
        SourceCapability.TRENDING,
        SourceCapability.RECOMMENDED,
        SourceCapability.CREATOR,
        SourceCapability.RELATED,
    },
    "reddit": {
        SourceCapability.ACTIVITY_IMPORT,
        SourceCapability.SEARCH,
        SourceCapability.TRENDING,
        SourceCapability.COMMUNITY,
        SourceCapability.RELATED,
    },
}


CONTENT_ROWS: dict[str, dict[str, Any]] = {
    "bilibili": {
        "bvid": "BV1stable",
        "title": "Stable Bilibili item",
        "owner": {"name": "UP"},
        "pubdate": 1_700_000_000,
    },
    "xiaohongshu": {
        "note_id": "xhs-stable",
        "title": "Stable XHS note",
        "author": {"nickname": "Author"},
    },
    "douyin": {
        "aweme_id": "dy-stable",
        "desc": "Stable Douyin video",
        "author": {"nickname": "Creator"},
    },
    "youtube": {
        "videoId": "yt-stable",
        "title": {"simpleText": "Stable YouTube video"},
        "ownerText": {"runs": [{"text": "Channel"}]},
    },
    "twitter": {
        "rest_id": "tw-stable",
        "full_text": "Stable tweet",
        "user": {"screen_name": "author"},
    },
    "zhihu": {
        "id": "zh-stable",
        "type": "answer",
        "title": "Stable Zhihu answer",
        "author": {"name": "Author"},
        "url": "https://www.zhihu.com/question/1/answer/zh-stable",
    },
    "reddit": {
        "name": "t3_rd-stable",
        "title": "Stable Reddit post",
        "author": "redditor",
        "permalink": "/r/python/comments/rd-stable/stable/",
    },
}


ACTIVITY_ROWS: dict[str, dict[str, Any]] = {
    "bilibili": {"event_type": "view", "bvid": "BV1event", "title": "Watched"},
    "xiaohongshu": {"scope": "saved", "note_id": "xhs-event", "title": "Saved"},
    "douyin": {"scope": "dy_like", "aweme_id": "dy-event", "desc": "Liked"},
    "youtube": {"scope": "yt_history", "video_id": "yt-event", "title": "Watched"},
    "zhihu": {"scope": "zhihu_collection", "id": "zh-event", "title": "Collected"},
    "reddit": {"scope": "reddit_upvoted", "name": "t3_rd-event", "title": "Upvoted"},
}


class FakeTransport:
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.calls: list[tuple[str, str | None, int]] = []

    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        self.calls.append((operation, query, limit))
        if operation == SourceCapability.ACTIVITY_IMPORT:
            return [ACTIVITY_ROWS[self.source_id]]
        return [CONTENT_ROWS[self.source_id]]


@pytest.fixture
def registry():  # type: ignore[no-untyped-def]
    transports = {source_id: FakeTransport(source_id) for source_id in EXPECTED_CAPABILITIES}
    return make_registry(transports)


def make_registry(transports: dict[str, FakeTransport]):  # type: ignore[no-untyped-def]
    return build_source_registry(
        bilibili=BilibiliConnector(transports["bilibili"]),
        xiaohongshu=XiaohongshuConnector(transports["xiaohongshu"]),
        douyin=DouyinConnector(transports["douyin"]),
        youtube=YouTubeConnector(transports["youtube"]),
        twitter=TwitterConnector(transports["twitter"]),
        zhihu=ZhihuConnector(transports["zhihu"]),
        reddit=RedditConnector(transports["reddit"]),
    )


def test_registry_is_explicit_and_contains_only_canonical_sources(registry: Any) -> None:
    assert registry.source_ids == tuple(EXPECTED_CAPABILITIES)
    assert set(registry.manifests) == set(EXPECTED_CAPABILITIES)


@pytest.mark.parametrize(("source_id", "capabilities"), EXPECTED_CAPABILITIES.items())
def test_manifests_report_only_retained_capabilities(
    registry: Any, source_id: str, capabilities: set[SourceCapability]
) -> None:
    manifest = registry.get(source_id).manifest

    assert manifest.source_id == source_id
    assert manifest.capabilities == frozenset(capabilities)


def test_source_specific_settings_are_strict_and_safe_defaults() -> None:
    settings = (
        BilibiliSettings(),
        XiaohongshuSettings(),
        DouyinSettings(),
        YouTubeSettings(),
        TwitterSettings(),
        ZhihuSettings(),
        RedditSettings(),
    )

    assert settings[0].enabled is True
    assert all(setting.model_config.get("extra") == "forbid" for setting in settings)
    assert all(
        "cookie" not in field.lower()
        for setting in settings
        for field in type(setting).model_fields
    )
    assert all(
        "token" not in field.lower() for setting in settings for field in type(setting).model_fields
    )


@pytest.mark.parametrize("source_id", EXPECTED_CAPABILITIES)
async def test_discovery_normalizes_to_immutable_content_with_stable_identity(
    registry: Any, source_id: str
) -> None:
    connector = registry.get(source_id)
    capability = next(
        capability
        for capability in connector.manifest.capabilities
        if capability is not SourceCapability.ACTIVITY_IMPORT
    )
    query = "python" if capability.requires_input else None

    first = await connector.discover(capability, query, 5)
    second = await connector.discover(capability, query, 5)

    assert len(first) == 1
    assert isinstance(first[0], ContentItem)
    assert first[0].source_id == source_id
    assert first[0].external_id
    assert first[0].id == second[0].id
    with pytest.raises(ValidationError):
        first[0].title = "mutated"  # type: ignore[misc]


@pytest.mark.parametrize("source_id", ACTIVITY_ROWS)
async def test_activity_import_normalizes_to_immutable_stable_events(
    registry: Any, source_id: str
) -> None:
    connector = registry.get(source_id)

    first = await connector.import_activity()
    second = await connector.import_activity()

    assert len(first) == 1
    assert isinstance(first[0], ActivityEvent)
    assert first[0].source_id == source_id
    assert first[0].content_external_id
    assert first[0].id == second[0].id
    assert first[0].occurred_at <= datetime.now(UTC)


@pytest.mark.parametrize("source_id", EXPECTED_CAPABILITIES)
async def test_unsupported_operations_are_rejected_not_emulated(
    registry: Any, source_id: str
) -> None:
    connector = registry.get(source_id)
    unsupported = next(
        capability
        for capability in SourceCapability
        if capability not in connector.manifest.capabilities
    )

    with pytest.raises(UnsupportedSourceOperationError):
        if unsupported is SourceCapability.ACTIVITY_IMPORT:
            await connector.import_activity()
        else:
            await connector.discover(unsupported, "seed", 5)


@pytest.mark.parametrize("source_id", EXPECTED_CAPABILITIES)
def test_connectors_expose_no_account_mutation_operations(registry: Any, source_id: str) -> None:
    connector = registry.get(source_id)

    for operation in ("like", "follow", "save", "favorite", "upvote", "subscribe"):
        assert not hasattr(connector, operation)
