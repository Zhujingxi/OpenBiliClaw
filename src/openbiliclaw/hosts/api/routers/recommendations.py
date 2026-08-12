from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import RecommendationPage, RefreshRequest, RefreshResponse

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("", response_model=RecommendationPage)
async def recommendations(
    limit: int = Query(default=20, ge=1, le=100),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> RecommendationPage:
    result = await dependencies.facade.get_recommendations(limit)
    return RecommendationPage(items=result.items)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    body: RefreshRequest,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> RefreshResponse:
    result = await dependencies.facade.refresh_recommendations(body.to_command())
    return RefreshResponse(decision=result.decision.value)
