"""Local favorites and watch-later collection routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.features.library.domain import CollectionItem, CollectionKind, LibraryItem


class SaveCollectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: UUID
    note: str = Field(default="", max_length=2000)


router = APIRouter(prefix="/library", tags=["library"], dependencies=[Depends(require_access)])


@router.get(
    "/{collection}",
    operation_id="v1_library_list",
    response_model=tuple[LibraryItem, ...],
)
def list_collection(
    collection: CollectionKind,
    container: Container,
) -> tuple[LibraryItem, ...]:
    return container.library.list(collection)


@router.post(
    "/{collection}",
    operation_id="v1_library_add",
    response_model=CollectionItem,
    status_code=status.HTTP_201_CREATED,
)
def add_collection_item(
    collection: CollectionKind,
    payload: SaveCollectionItem,
    container: Container,
) -> CollectionItem:
    return container.library.save(collection, payload.content_id, note=payload.note)


@router.delete(
    "/{collection}/{content_id}",
    operation_id="v1_library_remove",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_collection_item(
    collection: CollectionKind,
    content_id: UUID,
    container: Container,
) -> Response:
    if not container.library.remove(collection, content_id):
        raise LookupError("collection item not found")
    return Response(status_code=204)
