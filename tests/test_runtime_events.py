from __future__ import annotations

import asyncio

from openbiliclaw.runtime.events import RuntimeEventHub


async def test_runtime_event_hub_delivers_published_events() -> None:
    hub = RuntimeEventHub()

    queue = await hub.subscribe()
    await hub.publish({"type": "refresh.started", "message": "开始给你补候选了"})

    event = await asyncio.wait_for(queue.get(), timeout=0.2)
    assert event == {"type": "refresh.started", "message": "开始给你补候选了"}


async def test_runtime_event_hub_removes_unsubscribed_queue() -> None:
    hub = RuntimeEventHub()

    queue = await hub.subscribe()
    await hub.unsubscribe(queue)
    await hub.publish({"type": "runtime.idle"})

    assert queue.empty()
