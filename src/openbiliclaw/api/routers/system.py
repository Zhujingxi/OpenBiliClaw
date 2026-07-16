"""System readiness and stable LiteLLM alias health routes."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from openbiliclaw import __version__
from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.api.v1_models import AIHealthResponse


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ready: bool
    version: str


router = APIRouter(prefix="/system", tags=["system"])


@router.get("/readiness", operation_id="v1_system_readiness", response_model=ReadinessResponse)
def readiness() -> ReadinessResponse:
    return ReadinessResponse(ready=True, version=__version__)


@router.get(
    "/ai-health",
    operation_id="v1_system_ai_health",
    response_model=AIHealthResponse,
    dependencies=[Depends(require_access)],
)
async def ai_health(container: Container) -> AIHealthResponse:
    return AIHealthResponse.model_validate(
        (await container.ai_health.check_aliases()).model_dump(mode="json")
    )
