"""Local favorites and watch-later collection routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.features.library.domain import CollectionKind


class SaveCollectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: UUID
    note: str = Field(default="", max_length=2000)


router = APIRouter(prefix="/library", tags=["library"], dependencies=[Depends(require_access)])


@router.get("/{collection}", operation_id="v1_library_list")
def list_collection(
    collection: CollectionKind,
    container: Container,
) -> object:
    return jsonable_encoder(container.library.list(collection))


@router.post(
    "/{collection}",
    operation_id="v1_library_add",
    status_code=status.HTTP_201_CREATED,
)
def add_collection_item(
    collection: CollectionKind,
    payload: SaveCollectionItem,
    container: Container,
) -> object:
    return jsonable_encoder(
        container.library.save(collection, payload.content_id, note=payload.note)
    )


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
