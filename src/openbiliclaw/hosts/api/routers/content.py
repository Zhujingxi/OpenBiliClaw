from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import (
    ActionResultResponse,
    ConfirmActionRequest,
    ContentResponse,
    PendingActionResponse,
    ProposeActionRequest,
    SearchResponse,
)

router = APIRouter(prefix="/content", tags=["content"])


@router.get("/search", response_model=SearchResponse)
async def search(
    provider_id: str,
    q: str = Query(min_length=1, max_length=1000),
    limit: int = Query(default=20, ge=1, le=50),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> SearchResponse:
    result = await dependencies.facade.search_content(provider_id, q, limit)
    return SearchResponse(items=result.items)


@router.get("/{reference}", response_model=ContentResponse)
async def detail(
    reference: str,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> ContentResponse:
    return ContentResponse(
        content=(await dependencies.facade.get_content_details(reference)).content
    )


@router.post("/actions/propose", response_model=PendingActionResponse)
async def propose(
    body: ProposeActionRequest,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> PendingActionResponse:
    return PendingActionResponse(action=await dependencies.facade.propose_action(body.to_command()))


@router.post("/actions/confirm", response_model=ActionResultResponse)
async def confirm(
    body: ConfirmActionRequest,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> ActionResultResponse:
    return ActionResultResponse(result=await dependencies.facade.confirm_action(body.to_command()))
