from typing import Any

from openbiliclaw.features.activity.domain import ActivityKind
from openbiliclaw.features.sources.domain import SourceOperation
from openbiliclaw.infrastructure.sources.douyin import DouyinConnector


class Transport:
    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == "bootstrap_import":
            return [{"scope": "dy_follow", "creator_sec_uid": "creator-1", "nickname": "Creator"}]
        return [{"aweme_id": "aweme-1", "desc": "Video", "author": {"nickname": "Creator"}}]


async def test_douyin_transport_normalizes_browser_or_direct_rows() -> None:
    connector = DouyinConnector(Transport())

    events = await connector.import_activity()
    items = await connector.discover(SourceOperation.TRENDING, None, 3)

    assert events[0].kind is ActivityKind.FOLLOW
    assert str(items[0].url) == "https://www.douyin.com/video/aweme-1"
