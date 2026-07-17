"""Focused Task 21b tests for the renderable local-library contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import HttpUrl
from sqlalchemy import event

from openbiliclaw.api.dependencies import require_access
from openbiliclaw.api.routers.library import router as library_router
from openbiliclaw.features.feed.domain import ContentItem
from openbiliclaw.features.library.domain import (
    CollectionItem,
    CollectionKind,
    LibraryItem,
)
from openbiliclaw.infrastructure.database.base import (
    DatabaseSettings,
    create_engine_and_session,
)
from openbiliclaw.infrastructure.database.uow import UnitOfWork

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
CONTENT_A = UUID("00000000-0000-0000-0000-000000000301")
CONTENT_B = UUID("00000000-0000-0000-0000-000000000302")
MEMBERSHIP_A = UUID("00000000-0000-0000-0000-000000000311")
MEMBERSHIP_B = UUID("00000000-0000-0000-0000-000000000310")


def _url(path: Path) -> str:
    return f"sqlite:///{path}"


def test_library_read_joins_renderable_content_in_one_deterministic_query(
    tmp_path: Path,
) -> None:
    path = tmp_path / "library.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", _url(path))
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=_url(path)))
    first_content = ContentItem(
        id=CONTENT_A,
        source_id="bilibili",
        external_id="BV1joined-a",
        url=HttpUrl("https://www.bilibili.com/video/BV1joined-a"),
        title="Joined A",
        summary="Renderable summary",
        creator="Creator A",
        published_at=NOW,
        media_type="video",
        metadata={"thumbnail_url": "https://example.com/a.jpg"},
    )
    second_content = ContentItem(
        id=CONTENT_B,
        source_id="youtube",
        external_id="joined-b",
        url=HttpUrl("https://www.youtube.com/watch?v=joined-b"),
        title="Joined B",
    )
    first_membership = CollectionItem(
        id=MEMBERSHIP_A,
        collection=CollectionKind.FAVORITES,
        content_id=CONTENT_A,
        added_at=NOW,
        note="first",
    )
    second_membership = CollectionItem(
        id=MEMBERSHIP_B,
        collection=CollectionKind.FAVORITES,
        content_id=CONTENT_B,
        added_at=NOW,
        note="second",
    )
    with UnitOfWork(session_factory) as uow:
        uow.content.add(first_content)
        uow.content.add(second_content)
        uow.collections.add(first_membership)
        uow.collections.add(second_membership)
        uow.commit()

    selects: list[str] = []

    def record_query(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", record_query)
    try:
        with UnitOfWork(session_factory) as uow:
            items = uow.collections.list_items(CollectionKind.FAVORITES)
    finally:
        event.remove(engine, "before_cursor_execute", record_query)
        engine.dispose()

    assert items == (
        LibraryItem(collection_item=second_membership, content=second_content),
        LibraryItem(collection_item=first_membership, content=first_content),
    )
    assert items[1].content.metadata["thumbnail_url"] == "https://example.com/a.jpg"
    assert len(selects) == 1


def test_library_endpoint_returns_collection_metadata_and_renderable_content() -> None:
    content = ContentItem(
        id=CONTENT_A,
        source_id="bilibili",
        external_id="BV1browser",
        url=HttpUrl("https://www.bilibili.com/video/BV1browser"),
        title="Browser title",
        summary="Browser summary",
        creator="Browser creator",
        published_at=NOW,
        metadata={"thumbnail_url": "https://example.com/browser.jpg"},
    )
    membership = CollectionItem(
        id=MEMBERSHIP_A,
        collection=CollectionKind.FAVORITES,
        content_id=CONTENT_A,
        added_at=NOW,
        note="render me",
    )

    class LibraryPort:
        def list(self, collection: CollectionKind) -> tuple[LibraryItem, ...]:
            assert collection is CollectionKind.FAVORITES
            return (LibraryItem(collection_item=membership, content=content),)

    app = FastAPI()
    app.state.container = type("Container", (), {"library": LibraryPort()})()
    app.dependency_overrides[require_access] = lambda: None
    app.include_router(library_router, prefix="/api/v1")

    response = TestClient(app).get("/api/v1/library/favorites")

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["collection_item"]["note"] == "render me"
    assert payload["content"] == content.model_dump(mode="json")
