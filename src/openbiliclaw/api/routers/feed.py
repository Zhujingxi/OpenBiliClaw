"""Ordered personalized feed read route."""

from fastapi import APIRouter, Depends, Query

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.features.feed.domain import FeedItem

router = APIRouter(prefix="/feed", tags=["feed"], dependencies=[Depends(require_access)])


@router.get(
    "",
    operation_id="v1_feed_list",
    response_model=tuple[FeedItem, ...],
)
def list_feed(
    container: Container,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> tuple[FeedItem, ...]:
    return container.feed.list_entries(limit=limit, offset=offset)
