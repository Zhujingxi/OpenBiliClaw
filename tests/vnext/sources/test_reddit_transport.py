from typing import Any

from openbiliclaw.features.activity.domain import ActivityKind
from openbiliclaw.features.sources.domain import SourceOperation
from openbiliclaw.infrastructure.sources.reddit import RedditConnector


class Transport:
    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == "bootstrap_import":
            return [{"scope": "reddit_saved", "name": "t3_saved", "title": "Saved"}]
        return [
            {"name": "t3_post", "title": "Post", "permalink": "/r/python/comments/post/example/"}
        ]


async def test_reddit_transport_normalizes_cli_or_browser_rows() -> None:
    connector = RedditConnector(Transport())

    events = await connector.import_activity()
    items = await connector.discover(SourceOperation.COMMUNITY, "python", 3)

    assert events[0].kind is ActivityKind.FAVORITE
    assert str(items[0].url) == "https://www.reddit.com/r/python/comments/post/example/"
