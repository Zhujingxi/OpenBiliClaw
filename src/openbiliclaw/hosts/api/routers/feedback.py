from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import (
    FeedbackRequest,
    FeedbackResponse,
    ObservationsRequest,
    ObservationsResponse,
)

router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(
    body: FeedbackRequest,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> FeedbackResponse:
    return FeedbackResponse(result=await dependencies.facade.record_feedback(body.to_command()))


@router.post("/observations", response_model=ObservationsResponse)
async def observations(
    body: ObservationsRequest,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> ObservationsResponse:
    return ObservationsResponse(
        result=await dependencies.facade.record_observations(body.to_command())
    )
