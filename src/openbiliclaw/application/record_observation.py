"""Explicit observation import workflow over the sole ingress boundary."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.observations.models import Observation
from openbiliclaw.observations.service import RecordBatchResult


class ObservationIngressPort(Protocol):
    async def record_batch(
        self, observations: tuple[Observation, ...], *, allowed_event_types: frozenset[str]
    ) -> RecordBatchResult: ...


class RecordObservationsCommand(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: str = Field(min_length=8, max_length=200)
    observations: tuple[Observation, ...] = Field(min_length=1, max_length=100)
    allowed_event_types: frozenset[str] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class RecordObservations:
    ingress: ObservationIngressPort

    async def __call__(self, command: RecordObservationsCommand) -> RecordBatchResult:
        # Per-observation producer keys are authoritative; the command key is
        # mandatory host retry audit metadata and intentionally not rewritten.
        return await self.ingress.record_batch(
            command.observations, allowed_event_types=command.allowed_event_types
        )
