from typing import Any

from openbiliclaw.features.activity.domain import ActivityKind
from openbiliclaw.features.sources.domain import SourceOperation
from openbiliclaw.infrastructure.sources.zhihu import ZhihuConnector


class Transport:
    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == "bootstrap_import":
            return [{"scope": "zhihu_read_history", "id": "answer-1", "title": "Read"}]
        return [
            {"id": "answer-1", "type": "answer", "title": "Answer", "question_id": "question-1"}
        ]


async def test_zhihu_transport_normalizes_browser_task_rows() -> None:
    connector = ZhihuConnector(Transport())

    events = await connector.import_activity()
    items = await connector.discover(SourceOperation.RELATED, "https://www.zhihu.com/question/1", 3)

    assert events[0].kind is ActivityKind.VIEW
    assert str(items[0].url) == "https://www.zhihu.com/question/question-1/answer/answer-1"


class ExactRetainedShapeTransport:
    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == "bootstrap_import":
            return [
                {
                    "content_id": "42",
                    "content_type": "answer",
                    "interaction_action": "赞同了回答",
                    "author": "Alice",
                    "summary": "flat summary",
                    "title": "Answer",
                }
            ]
        return [
            {
                "content_id": "42",
                "content_type": "article",
                "author": "Bob",
                "summary": "flat discovery summary",
                "title": "Article",
            }
        ]


async def test_zhihu_exact_retained_shape_maps_action_flat_fields_and_typed_identity() -> None:
    connector = ZhihuConnector(ExactRetainedShapeTransport())
    event = (await connector.import_activity())[0]
    item = (await connector.discover(SourceOperation.SEARCH, "python", 3))[0]

    assert event.kind is ActivityKind.LIKE
    assert event.content_external_id == "answer:42"
    assert item.external_id == "article:42"
    assert item.creator == "Bob"
    assert item.summary == "flat discovery summary"
