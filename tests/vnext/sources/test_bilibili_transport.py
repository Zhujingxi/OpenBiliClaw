from typing import Any

from openbiliclaw.features.activity.domain import ActivityKind
from openbiliclaw.features.sources.domain import SourceCapability
from openbiliclaw.infrastructure.sources.bilibili import BilibiliConnector


class Transport:
    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == "activity_import":
            return [{"event_type": "favorite", "bvid": "BV1fav", "title": "Favorite"}]
        return [{"bvid": "BV1search", "title": "Search", "owner": {"name": "UP"}}]


async def test_bilibili_transport_normalizes_activity_and_search() -> None:
    connector = BilibiliConnector(Transport())

    events = await connector.import_activity()
    items = await connector.discover(SourceCapability.SEARCH, "python", 3)

    assert events[0].kind is ActivityKind.FAVORITE
    assert str(items[0].url) == "https://www.bilibili.com/video/BV1search"
