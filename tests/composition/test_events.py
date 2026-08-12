from __future__ import annotations

import asyncio

import pytest

from openbiliclaw.composition.events import ObservationEventSource
from openbiliclaw.infrastructure.events.publisher import EventPublisher
from openbiliclaw.observations.events import ObservationsCommitted


@pytest.mark.asyncio
async def test_observation_events_bridge_to_bounded_host_replay() -> None:
    publisher: EventPublisher[ObservationsCommitted] = EventPublisher()
    source = ObservationEventSource(publisher, limit=1)
    await source.start()
    assert await source.ready()
    await publisher.publish(ObservationsCommitted(("obs_one",)))
    await asyncio.sleep(0)
    first = await source.replay(0, 10)
    assert len(first) == 1
    assert first[0].status == "committed:1"
    await publisher.publish(ObservationsCommitted(("obs_two", "obs_three")))
    await asyncio.sleep(0)
    assert tuple(item.event_id for item in await source.replay(0, 10)) == (2,)
    await source.stop()
    assert not await source.ready()
    await publisher.close()
