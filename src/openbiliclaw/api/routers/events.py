"""Normalized activity ingestion route."""

from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.api.v1_models import EventIngestResponse
from openbiliclaw.features.activity.domain import ActivityEvent

router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(require_access)])


@router.post(
    "",
    operation_id="v1_events_ingest",
    response_model=None,
    responses={status.HTTP_202_ACCEPTED: {"model": EventIngestResponse}},
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_event(
    event: ActivityEvent,
    container: Container,
) -> object:
    return {
        "event_id": str(event.id),
        "signals": jsonable_encoder(container.activity.ingest(event)),
    }
