from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openbiliclaw.features.sources.domain import SourceOperation
from openbiliclaw.infrastructure.sources.browser_tasks import QueuedBrowserTransport

from .test_browser_tasks import task_context  # noqa: F401


async def test_queue_transport_awaits_a_completed_typed_result(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    _, _, service = task_context
    transport = QueuedBrowserTransport(
        service, "zhihu", timeout_seconds=1, poll_interval_seconds=0.001
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    claim = None
    while claim is None:
        claim = await asyncio.to_thread(service.claim, "zhihu")
        await asyncio.sleep(0)
    await asyncio.to_thread(
        service.complete,
        claim.id,
        claim.lease_token,
        {"items": [{"content_id": "1", "content_type": "answer"}]},
    )
    assert await pending == [{"content_id": "1", "content_type": "answer"}]


async def test_queue_transport_has_a_bounded_wait(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    _, _, service = task_context
    transport = QueuedBrowserTransport(
        service, "zhihu", timeout_seconds=0.01, poll_interval_seconds=0.001
    )
    with pytest.raises(TimeoutError):
        await transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
