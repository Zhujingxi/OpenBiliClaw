from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from openbiliclaw.content.integration.identity import ContentRef

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import (
    ActionResultResponse,
    ConfirmActionRequest,
    ContentResponse,
    NativeContentView,
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


@router.get("/detail", response_model=ContentResponse)
async def detail(
    reference: str = Query(min_length=1, max_length=4096),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> ContentResponse:
    """Fetch details for a JSON-serialized content reference."""
    try:
        content_ref = ContentRef.model_validate_json(reference)
    except ValidationError as exc:
        raise HTTPException(status_code=422) from exc
    result = await dependencies.facade.get_content_details(content_ref.model_dump_json())
    native = result.content
    return ContentResponse(
        content=NativeContentView(
            ref=native.ref,
            schema_version=native.schema_version,
            payload=dict(native.payload.model_dump(mode="json")),
        )
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
