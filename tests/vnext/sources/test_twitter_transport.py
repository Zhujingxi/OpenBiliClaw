from typing import Any

from openbiliclaw.features.sources.domain import (
    SourceOperation,
)
from openbiliclaw.infrastructure.sources.twitter import TwitterConnector


class Transport:
    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        return [{"rest_id": "tweet-1", "full_text": "Tweet", "user": {"screen_name": "author"}}]


async def test_twitter_transport_is_read_only_discovery_without_fake_bootstrap() -> None:
    connector = TwitterConnector(Transport())

    items = await connector.discover(SourceOperation.FEED, None, 3)

    assert str(items[0].url) == "https://x.com/author/status/tweet-1"
    events = await connector.import_activity()
    assert events
