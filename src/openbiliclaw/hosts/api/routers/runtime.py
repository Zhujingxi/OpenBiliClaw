from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import HostDependencies, get_dependencies
from ..schemas.models import RuntimeResponse

router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.get("/health", response_model=RuntimeResponse)
async def health(
    dependencies: HostDependencies = Depends(get_dependencies),
) -> RuntimeResponse:
    return RuntimeResponse(health=(await dependencies.facade.job_health()).health)
