"""Local-only collection use cases."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from openbiliclaw.features.library.domain import CollectionItem, CollectionKind, LibraryItem

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType
    from uuid import UUID


class CollectionRepository(Protocol):
    def add(self, item: CollectionItem) -> CollectionItem: ...

    def remove(self, collection: CollectionKind, content_id: UUID) -> bool: ...

    def list_items(self, collection: CollectionKind) -> tuple[LibraryItem, ...]: ...


class LibraryUnitOfWork(Protocol):
    collections: CollectionRepository

    def __enter__(self) -> LibraryUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class LibraryService:
    """Mutate only the two predefined application-local collections."""

    def __init__(self, uow_factory: Callable[[], LibraryUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def list(self, collection: CollectionKind) -> tuple[LibraryItem, ...]:
        """List one predefined local-only collection."""

        with self._uow_factory() as uow:
            return uow.collections.list_items(collection)

    def save(
        self, collection: CollectionKind, content_id: UUID, *, note: str = ""
    ) -> CollectionItem:
        item = CollectionItem(collection=collection, content_id=content_id, note=note)
        with self._uow_factory() as uow:
            stored = uow.collections.add(item)
            uow.commit()
        return stored

    def remove(self, collection: CollectionKind, content_id: UUID) -> bool:
        with self._uow_factory() as uow:
            removed = uow.collections.remove(collection, content_id)
            uow.commit()
        return removed


__all__ = ["LibraryService"]
