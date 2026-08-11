"""Typed bounded in-process post-commit notifications."""

from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

EventT = TypeVar("EventT")


class _Closed:
    pass


_CLOSED = _Closed()


class EventSubscription(Generic[EventT]):
    """One bounded typed event subscription."""

    def __init__(
        self, publisher: EventPublisher[EventT], queue: asyncio.Queue[EventT | _Closed]
    ) -> None:
        self._publisher = publisher
        self._queue = queue
        self._closed = False

    async def receive(self) -> EventT:
        """Wait for the next notification."""

        item = await self._queue.get()
        if isinstance(item, _Closed):
            raise EOFError("event subscription is closed")
        return item

    async def close(self) -> None:
        """Unsubscribe idempotently."""

        if not self._closed:
            self._closed = True
            self._publisher._unsubscribe(self._queue)


class EventPublisher(Generic[EventT]):
    """Typed bounded fan-out for notifications emitted after commit."""

    def __init__(self, *, queue_size: int = 64) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be positive")
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[EventT | _Closed]] = set()
        self._publish_lock = asyncio.Lock()
        self._closed = False

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> EventSubscription[EventT]:
        """Register a bounded subscriber."""

        if self._closed:
            raise RuntimeError("event publisher is closed")
        queue: asyncio.Queue[EventT | _Closed] = asyncio.Queue(self._queue_size)
        self._subscribers.add(queue)
        return EventSubscription(self, queue)

    async def publish(self, event: EventT) -> None:
        """Notify current subscribers, applying bounded backpressure."""

        async with self._publish_lock:
            if self._closed:
                raise RuntimeError("event publisher is closed")
            for queue in tuple(self._subscribers):
                await queue.put(event)

    async def close(self) -> None:
        """Close all subscriptions without leaking blocked receivers."""

        if self._closed:
            return
        self._closed = True
        # Drain first so a bounded publish can finish and release the lock.
        for queue in self._subscribers:
            while not queue.empty():
                queue.get_nowait()
        async with self._publish_lock:
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
            for queue in subscribers:
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(_CLOSED)

    def _unsubscribe(self, queue: asyncio.Queue[EventT | _Closed]) -> None:
        self._subscribers.discard(queue)
        while not queue.empty():
            queue.get_nowait()
