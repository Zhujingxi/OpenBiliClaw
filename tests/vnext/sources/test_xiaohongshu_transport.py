from typing import Any

from openbiliclaw.features.activity.domain import ActivityKind
from openbiliclaw.features.sources.domain import SourceOperation
from openbiliclaw.infrastructure.sources.xiaohongshu import XiaohongshuConnector


class Transport:
    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == "bootstrap_import":
            return [{"scope": "liked", "note_id": "liked-note", "title": "Liked"}]
        return [{"note_id": "note-1", "title": "Note", "author": {"nickname": "Writer"}}]


async def test_xiaohongshu_transport_normalizes_browser_rows() -> None:
    connector = XiaohongshuConnector(Transport())

    events = await connector.import_activity()
    items = await connector.discover(SourceOperation.CREATOR, "writer-id", 3)

    assert events[0].kind is ActivityKind.LIKE
    assert str(items[0].url) == "https://www.xiaohongshu.com/explore/note-1"
