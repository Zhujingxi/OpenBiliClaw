from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from openbiliclaw.infrastructure.events.publisher import EventPublisher
from openbiliclaw.infrastructure.telemetry import TelemetrySink


@dataclass(frozen=True)
class Done:
    value: int


async def test_typed_publisher_subscribe_publish_and_close() -> None:
    publisher = EventPublisher[Done](queue_size=1)
    subscription = publisher.subscribe()
    await publisher.publish(Done(1))
    assert await subscription.receive() == Done(1)
    await subscription.close()
    assert publisher.subscriber_count == 0
    await publisher.close()
    with pytest.raises(RuntimeError, match="closed"):
        publisher.subscribe()


async def test_closed_subscription_and_idempotent_close() -> None:
    publisher = EventPublisher[Done]()
    subscription = publisher.subscribe()
    await subscription.close()
    await subscription.close()
    await publisher.close()
    await publisher.close()
    with pytest.raises(EOFError):
        orphan = EventPublisher[Done]()
        waiting = orphan.subscribe()
        await orphan.close()
        await waiting.receive()
    with pytest.raises(ValueError):
        EventPublisher[Done](queue_size=0)


async def test_unsubscribe_unblocks_bounded_publisher() -> None:
    publisher = EventPublisher[Done](queue_size=1)
    subscription = publisher.subscribe()
    await publisher.publish(Done(1))
    blocked = asyncio.create_task(publisher.publish(Done(2)))
    await asyncio.sleep(0)
    await subscription.close()
    await blocked
    assert publisher.subscriber_count == 0
    await publisher.close()


async def test_slow_subscriber_is_bounded() -> None:
    publisher = EventPublisher[Done](queue_size=1)
    subscription = publisher.subscribe()
    await publisher.publish(Done(1))
    publish = asyncio.create_task(publisher.publish(Done(2)))
    await asyncio.sleep(0)
    assert not publish.done()
    assert await subscription.receive() == Done(1)
    await publish
    await publisher.close()


def test_telemetry_records_content_free_exception_outcome() -> None:
    sink = TelemetrySink()
    with pytest.raises(RuntimeError), sink.trace("failure", {}):
        raise RuntimeError("sensitive detail")
    assert "sensitive detail" not in repr(sink.records)
    assert dict(sink.records[-1].fields) == {"error_type": "RuntimeError", "outcome": "error"}


def test_telemetry_retention_is_bounded() -> None:
    sink = TelemetrySink(max_records=2)
    sink.metric("first", {})
    sink.metric("second", {})
    sink.metric("third", {})
    assert [record.name for record in sink.records] == ["second", "third"]
    with pytest.raises(ValueError):
        TelemetrySink(max_records=0)


def test_telemetry_mandatorily_redacts_keys_and_registered_secrets() -> None:
    sink = TelemetrySink(secret_values=("actual-secret",))
    record = sink.metric(
        "request", {"api_key": "actual-secret", "route": "/actual-secret", "count": 1}
    )
    rendered = repr(record)
    assert "actual-secret" not in rendered
    assert dict(record.fields) == {"api_key": "<redacted>", "route": "/<redacted>", "count": 1}
    with sink.trace("operation", {"token": "hidden"}) as trace:
        assert dict(trace.fields)["token"] == "<redacted>"
    assert len(sink.records) == 3
