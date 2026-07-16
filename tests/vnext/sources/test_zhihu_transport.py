from typing import Any

from openbiliclaw.features.activity.domain import ActivityKind
from openbiliclaw.features.sources.domain import SourceCapability
from openbiliclaw.infrastructure.sources.zhihu import ZhihuConnector


class Transport:
    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == "activity_import":
            return [{"scope": "zhihu_read_history", "id": "answer-1", "title": "Read"}]
        return [
            {"id": "answer-1", "type": "answer", "title": "Answer", "question_id": "question-1"}
        ]


async def test_zhihu_transport_normalizes_browser_task_rows() -> None:
    connector = ZhihuConnector(Transport())

    events = await connector.import_activity()
    items = await connector.discover(
        SourceCapability.RELATED, "https://www.zhihu.com/question/1", 3
    )

    assert events[0].kind is ActivityKind.VIEW
    assert str(items[0].url) == "https://www.zhihu.com/question/question-1/answer/answer-1"
