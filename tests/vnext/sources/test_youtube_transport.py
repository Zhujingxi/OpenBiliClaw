from typing import Any

from openbiliclaw.features.activity.domain import ActivityKind
from openbiliclaw.features.sources.domain import SourceCapability
from openbiliclaw.infrastructure.sources.youtube import YouTubeConnector


class Transport:
    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == "activity_import":
            return [{"scope": "yt_subscriptions", "channel_id": "UC1", "title": "Channel"}]
        return [{"videoId": "video-1", "title": {"runs": [{"text": "Video"}]}}]


async def test_youtube_transport_normalizes_takeout_browser_and_scraper_rows() -> None:
    connector = YouTubeConnector(Transport())

    events = await connector.import_activity()
    items = await connector.discover(SourceCapability.CREATOR, "UC1", 3)

    assert events[0].kind is ActivityKind.FOLLOW
    assert str(items[0].url) == "https://www.youtube.com/watch?v=video-1"
