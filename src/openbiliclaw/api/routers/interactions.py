"""Feed feedback and interaction writes."""

from fastapi import APIRouter, Depends, status

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.api.v1_models import InteractionResponse, InteractionResult
from openbiliclaw.features.feed.domain import Interaction

router = APIRouter(
    prefix="/interactions", tags=["interactions"], dependencies=[Depends(require_access)]
)


@router.post(
    "",
    operation_id="v1_interactions_create",
    response_model=InteractionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_interaction(
    interaction: Interaction,
    container: Container,
) -> InteractionResult:
    return {
        "interaction": interaction,
        "signal": container.feedback.record(interaction),
    }
