"""Bridge typed domain notifications to the bounded host event feed."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING

from openbiliclaw.hosts.api.schemas.models import EventEnvelope, JobEvent

if TYPE_CHECKING:
    from openbiliclaw.infrastructure.events.publisher import EventPublisher, EventSubscription
    from openbiliclaw.observations.events import ObservationsCommitted


class ObservationEventSource:
    """Retain a bounded, payload-free replay window for host streams."""

    def __init__(
        self, publisher: EventPublisher[ObservationsCommitted], *, limit: int = 1000
    ) -> None:
        self._publisher = publisher
        self._events: deque[EventEnvelope] = deque(maxlen=limit)
        self._subscription: EventSubscription[ObservationsCommitted] | None = None
        self._task: asyncio.Task[None] | None = None
        self._next_id = 1

    async def start(self) -> None:
        self._subscription = self._publisher.subscribe()
        self._task = asyncio.create_task(self._consume(), name="openbiliclaw:host-events")

    async def stop(self) -> None:
        if self._subscription is not None:
            await self._subscription.close()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._subscription = None
        self._task = None

    async def ready(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _consume(self) -> None:
        assert self._subscription is not None
        while True:
            committed = await self._subscription.receive()
            self._events.append(
                JobEvent(
                    event_id=self._next_id,
                    component_id="observations",
                    status=f"committed:{len(committed.observation_ids)}",
                )
            )
            self._next_id += 1

    async def replay(self, after: int, limit: int) -> tuple[EventEnvelope, ...]:
        return tuple(item for item in self._events if item.event_id > after)[:limit]
