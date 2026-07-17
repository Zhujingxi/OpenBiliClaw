"""Feed feedback and interaction writes."""

from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.api.v1_models import InteractionResponse
from openbiliclaw.features.feed.domain import Interaction

router = APIRouter(
    prefix="/interactions", tags=["interactions"], dependencies=[Depends(require_access)]
)


@router.post(
    "",
    operation_id="v1_interactions_create",
    response_model=None,
    responses={status.HTTP_201_CREATED: {"model": InteractionResponse}},
    status_code=status.HTTP_201_CREATED,
)
def create_interaction(
    interaction: Interaction,
    container: Container,
) -> object:
    return {
        "interaction": interaction.model_dump(mode="json"),
        "signal": jsonable_encoder(container.feedback.record(interaction)),
    }
