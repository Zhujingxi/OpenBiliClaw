"""Observation ingress validation, persistence, and post-commit publication."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .events import ObservationsCommitted
from .models import Observation, observation_adapter
from .repository import InsertStatus, ObservationPage, ObservationRepository

if TYPE_CHECKING:
    from openbiliclaw.infrastructure.events.publisher import EventPublisher

    from .validation import ObservationValidator


class RecordStatus(StrEnum):
    INSERTED = "inserted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RecordItemResult:
    index: int
    status: RecordStatus
    observation_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RecordBatchResult:
    items: tuple[RecordItemResult, ...]


class ObservationIngressService:
    """The sole typed ingress; partial validation failures do not abort valid rows."""

    def __init__(
        self,
        repository: ObservationRepository,
        publisher: EventPublisher[ObservationsCommitted],
        validator: ObservationValidator,
        *,
        maximum_batch_size: int = 100,
        maximum_serialized_bytes: int = 64_000,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._validator = validator
        self._maximum_batch_size = maximum_batch_size
        self._maximum_serialized_bytes = maximum_serialized_bytes

    async def record_batch(
        self,
        observations: tuple[Observation, ...],
        *,
        allowed_event_types: frozenset[str],
    ) -> RecordBatchResult:
        if not observations or len(observations) > self._maximum_batch_size:
            raise ValueError("batch size must be within configured bounds")
        accepted: list[Observation] = []
        accepted_indexes: list[int] = []
        results: dict[int, RecordItemResult] = {}
        for index, event in enumerate(observations):
            if len(observation_adapter.dump_json(event)) > self._maximum_serialized_bytes:
                results[index] = RecordItemResult(
                    index, RecordStatus.REJECTED, reason="payload_too_large"
                )
                continue
            validation = self._validator.validate(event, allowed_event_types=allowed_event_types)
            if not validation.accepted:
                results[index] = RecordItemResult(
                    index, RecordStatus.REJECTED, reason=validation.code.value
                )
                continue
            accepted.append(event)
            accepted_indexes.append(index)
        stored = await self._repository.insert_batch(tuple(accepted)) if accepted else ()
        committed: list[str] = []
        for index, item in zip(accepted_indexes, stored, strict=True):
            status = (
                RecordStatus.INSERTED
                if item.status == InsertStatus.INSERTED
                else RecordStatus.DUPLICATE
            )
            results[index] = RecordItemResult(index, status, observation_id=item.observation_id)
            if status is RecordStatus.INSERTED:
                committed.append(item.observation_id)
        if committed:
            await self._publisher.publish(ObservationsCommitted(tuple(committed)))
        return RecordBatchResult(tuple(results[index] for index in range(len(observations))))

    async def query(self, *, after_cursor: str | None, limit: int) -> ObservationPage:
        return await self._repository.read(after_cursor=after_cursor, limit=limit)
