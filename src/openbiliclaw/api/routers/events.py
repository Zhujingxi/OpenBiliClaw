"""Normalized activity ingestion route."""

from fastapi import APIRouter, Depends, status

from openbiliclaw.api.dependencies import Container, require_access
from openbiliclaw.api.v1_models import EventIngestResponse, EventIngestResult
from openbiliclaw.features.activity.domain import ActivityEvent

router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(require_access)])


@router.post(
    "",
    operation_id="v1_events_ingest",
    response_model=EventIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_event(
    event: ActivityEvent,
    container: Container,
) -> EventIngestResult:
    return {
        "event_id": event.id,
        "signals": container.activity.ingest(event),
    }
