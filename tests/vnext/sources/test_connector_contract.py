"""Shared manifest and executable-operation contracts for every retained source."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from openbiliclaw.features.activity.domain import ActivityEvent
from openbiliclaw.features.feed.domain import ContentItem
from openbiliclaw.features.sources.domain import (
    SourceCapability,
    SourceOperation,
    SourceResultKind,
    SourceTransportKind,
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

SOURCE_IDS = ("bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu", "reddit")

CONTENT_ROWS: dict[str, dict[str, Any]] = {
    "bilibili": {"bvid": "BV1stable", "title": "Bilibili"},
    "xiaohongshu": {"note_id": "xhs-stable", "title": "XHS"},
    "douyin": {"aweme_id": "dy-stable", "desc": "Douyin"},
    "youtube": {"videoId": "yt-stable", "title": "YouTube"},
    "twitter": {"id": "tw-stable", "text": "Tweet", "author": {"screenName": "author"}},
    "zhihu": {"content_id": "same", "content_type": "answer", "title": "Zhihu"},
    "reddit": {"name": "t3_stable", "title": "Reddit"},
}
ACTIVITY_ROWS = {
    "bilibili": {"bvid": "BV1event", "event_type": "view"},
    "xiaohongshu": {"note_id": "xhs-event", "scope": "liked"},
    "douyin": {"aweme_id": "dy-event", "scope": "dy_like"},
    "youtube": {"video_id": "yt-event", "scope": "yt_history"},
    "twitter": {"id": "tw-event", "scope": "liked", "text": "Tweet"},
    "zhihu": {
        "content_id": "zh-event",
        "content_type": "answer",
        "interaction_action": "收藏了回答",
    },
    "reddit": {"name": "t3_event", "scope": "reddit_saved"},
}


class RecordingTransport:
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.calls: list[tuple[str, str | None, int]] = []

    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        self.calls.append((operation, query, limit))
        if operation == SourceOperation.BOOTSTRAP_IMPORT:
            return [ACTIVITY_ROWS[self.source_id]]
        return [CONTENT_ROWS[self.source_id]]


def make_registry(transports: dict[str, RecordingTransport]):  # type: ignore[no-untyped-def]
    return build_source_registry(
        bilibili=BilibiliConnector(transports["bilibili"]),
        xiaohongshu=XiaohongshuConnector(transports["xiaohongshu"]),
        douyin=DouyinConnector(transports["douyin"]),
        youtube=YouTubeConnector(transports["youtube"]),
        twitter=TwitterConnector(transports["twitter"]),
        zhihu=ZhihuConnector(transports["zhihu"]),
        reddit=RedditConnector(transports["reddit"]),
    )


@pytest.fixture
def registry():  # type: ignore[no-untyped-def]
    return make_registry({source_id: RecordingTransport(source_id) for source_id in SOURCE_IDS})


def test_registry_contains_exactly_the_seven_retained_sources(registry: Any) -> None:
    assert registry.source_ids == SOURCE_IDS


def test_manifests_separate_capabilities_from_operations_and_transport_metadata(
    registry: Any,
) -> None:
    for manifest in registry.manifests.values():
        assert all(isinstance(capability, SourceCapability) for capability in manifest.capabilities)
        assert all(spec.capability in manifest.capabilities for spec in manifest.operations)
        assert len({spec.operation for spec in manifest.operations}) == len(manifest.operations)
        assert all(
            isinstance(spec.transport_kind, SourceTransportKind) for spec in manifest.operations
        )
    assert SourceOperation.RELATED not in {
        spec.operation
        for spec in registry.get("bilibili").manifest.operations
        if spec.operation.value == "explore"
    }


@pytest.mark.parametrize("source_id", SOURCE_IDS)
async def test_every_advertised_operation_executes_and_returns_its_declared_type(
    registry: Any, source_id: str
) -> None:
    connector = registry.get(source_id)
    for spec in connector.manifest.operations:
        query = "seed" if spec.operation.requires_input else None
        result = await connector.execute(spec.operation, query, 5)
        assert result
        expected = ActivityEvent if spec.result_kind is SourceResultKind.ACTIVITY else ContentItem
        assert all(isinstance(item, expected) for item in result)


@pytest.mark.parametrize("source_id", SOURCE_IDS)
async def test_all_unadvertised_operation_variants_are_rejected(
    registry: Any, source_id: str
) -> None:
    connector = registry.get(source_id)
    advertised = {spec.operation for spec in connector.manifest.operations}
    for operation in set(SourceOperation) - advertised:
        with pytest.raises(UnsupportedSourceOperationError):
            await connector.execute(operation, "seed", 5)


def test_all_settings_are_closed_and_reject_arbitrary_modes() -> None:
    settings = (
        BilibiliSettings(),
        XiaohongshuSettings(),
        DouyinSettings(),
        YouTubeSettings(),
        TwitterSettings(),
        ZhihuSettings(),
        RedditSettings(),
    )
    assert all(setting.model_config.get("extra") == "forbid" for setting in settings)
    with pytest.raises(ValidationError):
        DouyinSettings(mode="whatever")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ZhihuSettings(source_modes=("whatever",))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RedditSettings(source_modes=("whatever",))  # type: ignore[arg-type]


def test_connectors_expose_no_account_mutations(registry: Any) -> None:
    for connector in (registry.get(source_id) for source_id in SOURCE_IDS):
        for operation in ("like", "follow", "save", "favorite", "upvote", "subscribe"):
            assert not hasattr(connector, operation)
