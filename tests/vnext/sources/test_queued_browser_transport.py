from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from sqlalchemy import select

from openbiliclaw.features.sources.domain import SourceOperation, SourceTaskStatus
from openbiliclaw.infrastructure.database.models import SourceTaskModel
from openbiliclaw.infrastructure.sources.browser_tasks import QueuedBrowserTransport

from .test_browser_tasks import task_context  # noqa: F401


async def test_queue_transport_awaits_a_completed_typed_result(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
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
    session_factory, _, service = task_context
    transport = QueuedBrowserTransport(
        service, "zhihu", timeout_seconds=0.01, poll_interval_seconds=0.001
    )
    with pytest.raises(TimeoutError):
        await transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    with session_factory() as session:
        rows = list(session.scalars(select(SourceTaskModel)))
    assert len(rows) == 1
    assert rows[0].status == SourceTaskStatus.CANCELLED.value
    assert service.claim("zhihu") is None

    retry_transport = QueuedBrowserTransport(
        service, "zhihu", timeout_seconds=1, poll_interval_seconds=0.001
    )
    retry = asyncio.create_task(
        retry_transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    claim = None
    while claim is None:
        claim = await asyncio.to_thread(service.claim, "zhihu")
        await asyncio.sleep(0)
    await asyncio.to_thread(
        service.complete,
        claim.id,
        claim.lease_token,
        {"items": [{"content_id": "retry", "content_type": "answer"}]},
    )
    assert await retry
    with session_factory() as session:
        statuses = sorted(session.scalars(select(SourceTaskModel.status)))
    assert statuses == [SourceTaskStatus.CANCELLED.value, SourceTaskStatus.COMPLETED.value]


async def test_explicit_async_cancellation_compensates_the_durable_task(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    transport = QueuedBrowserTransport(
        service, "zhihu", timeout_seconds=1, poll_interval_seconds=0.001
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    while True:
        with session_factory() as session:
            row = session.scalar(select(SourceTaskModel))
        if row is not None:
            break
        await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    with session_factory() as session:
        row = session.scalar(select(SourceTaskModel))
        assert row is not None
        assert row.status == SourceTaskStatus.CANCELLED.value
    assert service.claim("zhihu") is None


async def test_cancellation_during_enqueue_leaves_only_a_terminal_row(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    started = threading.Event()
    release = threading.Event()

    class DelayedService:
        def enqueue(self, request: Any, *, task_id: Any) -> Any:
            started.set()
            release.wait(timeout=1)
            return service.enqueue(request, task_id=task_id)

        def cancel(self, task_id: Any) -> Any:
            return service.cancel(task_id)

        def snapshot(self, task_id: Any) -> Any:
            return service.snapshot(task_id)

    transport = QueuedBrowserTransport(
        DelayedService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    await asyncio.to_thread(started.wait, 1)
    pending.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    with session_factory() as session:
        rows = list(session.scalars(select(SourceTaskModel)))
    assert len(rows) == 1
    assert rows[0].status == SourceTaskStatus.CANCELLED.value
