from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.infrastructure.events.publisher import EventPublisher
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.observations.models import (
    PreferencePayload,
    PreferenceStatementObservation,
    RecommendationLikedObservation,
)
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.observations.repository import InsertStatus, SqliteObservationRepository
from openbiliclaw.observations.service import ObservationIngressService, RecordStatus
from openbiliclaw.observations.validation import ObservationValidator

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.observations.events import ObservationsCommitted

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def event(
    index: int,
    *,
    occurred_at: datetime = NOW,
    producer_id: str = "builtin.feedback",
) -> RecommendationLikedObservation:
    return RecommendationLikedObservation(
        observation_id="obs_" + f"{index:032x}",
        idempotency_key=f"event-{index}",
        occurred_at=occurred_at,
        received_at=NOW,
        account_id="account-1",
        content_ref=ContentRef(
            provider_id=ProviderId(value="bilibili"),
            content_kind=ContentKind(value="video"),
            provider_content_id=f"BV{index}",
            canonical_url=f"https://www.bilibili.com/video/BV{index}",
        ),
        provenance=ObservationProvenance(
            producer_id=producer_id,
            source=ObservationSource.RECOMMENDATION,
            authenticated=True,
            trust_level=TrustLevel.HIGH,
        ),
        payload={},
    )


async def setup(
    path: Path,
) -> tuple[SqliteDatabase, ObservationIngressService, EventPublisher[ObservationsCommitted]]:
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    publisher: EventPublisher[ObservationsCommitted] = EventPublisher()
    service = ObservationIngressService(
        SqliteObservationRepository(database),
        publisher,
        ObservationValidator(now=lambda: NOW),
    )
    return database, service, publisher


async def test_commit_duplicate_partial_failure_and_publish_after_commit(tmp_path: Path) -> None:
    database, service, publisher = await setup(tmp_path / "events.db")
    subscription = publisher.subscribe()
    invalid = event(2).model_copy(update={"content_ref": None})
    receipt = await service.record_batch(
        (event(1), event(1), invalid),
        allowed_event_types=frozenset({"recommendation_liked"}),
    )
    assert [item.status for item in receipt.items] == [
        RecordStatus.INSERTED,
        RecordStatus.DUPLICATE,
        RecordStatus.REJECTED,
    ]
    notification = await subscription.receive()
    assert notification.observation_ids == (event(1).observation_id,)
    page = await service.query(after_cursor=None, limit=10)
    assert page.items == (event(1),)
    await subscription.close()
    await publisher.close()
    await database.close()


async def test_out_of_order_cursor_replay_and_restart_are_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    database, service, publisher = await setup(path)
    await service.record_batch(
        (event(2), event(1)), allowed_event_types=frozenset({"recommendation_liked"})
    )
    first = await service.query(after_cursor=None, limit=1)
    second = await service.query(after_cursor=first.next_cursor, limit=1)
    assert first.items == (event(2),)
    assert second.items == (event(1),)
    await publisher.close()
    await database.close()

    database2 = SqliteDatabase(path)
    await database2.open()
    repository2 = SqliteObservationRepository(database2)
    replay = await repository2.read(after_cursor=None, limit=10)
    assert replay.items == (event(2), event(1))
    await database2.close()


async def test_transaction_rollback_publishes_nothing(tmp_path: Path) -> None:
    database, service, publisher = await setup(tmp_path / "events.db")
    subscription = publisher.subscribe()
    bad = event(2).model_copy(update={"observation_id": event(1).observation_id})
    with pytest.raises(sqlite3.IntegrityError):
        await service.record_batch(
            (event(1), bad), allowed_event_types=frozenset({"recommendation_liked"})
        )
    assert await service.query(after_cursor=None, limit=10) == await service.query(
        after_cursor=None, limit=10
    )
    assert (await service.query(after_cursor=None, limit=10)).items == ()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(subscription.receive(), timeout=0.01)
    await subscription.close()
    await publisher.close()
    await database.close()


async def test_batch_and_payload_bounds_reject_before_storage(tmp_path: Path) -> None:
    database, service, publisher = await setup(tmp_path / "events.db")
    too_many = tuple(event(index) for index in range(101))
    with pytest.raises(ValueError, match="batch"):
        await service.record_batch(
            too_many, allowed_event_types=frozenset({"recommendation_liked"})
        )
    oversized = PreferenceStatementObservation(
        observation_id="obs_" + "e" * 32,
        idempotency_key="oversized-1",
        occurred_at=NOW,
        received_at=NOW,
        account_id="account-1",
        content_ref=None,
        provenance=ObservationProvenance(
            producer_id="builtin.assistant",
            source=ObservationSource.ASSISTANT,
            authenticated=True,
            trust_level=TrustLevel.HIGH,
        ),
        # Bypass field construction only to exercise the service's independent
        # serialized trust-boundary ceiling with an actual >64 KiB item.
        payload=PreferencePayload.model_validate({"statement": "x"}).model_copy(
            update={"statement": "x" * 65_000}
        ),
    )
    receipt = await service.record_batch(
        (oversized,), allowed_event_types=frozenset({"preference_statement"})
    )
    assert receipt.items[0].status is RecordStatus.REJECTED
    assert receipt.items[0].reason == "payload_too_large"
    assert (await service.query(after_cursor=None, limit=10)).items == ()
    await publisher.close()
    await database.close()


async def test_idempotency_is_namespaced_by_producer(tmp_path: Path) -> None:
    database, _service, publisher = await setup(tmp_path / "events.db")
    repository = SqliteObservationRepository(database)
    first = event(1)
    other = event(2, producer_id="extension.feedback").model_copy(
        update={"idempotency_key": first.idempotency_key}
    )
    results = await repository.insert_batch((first, other))
    assert tuple(result.status for result in results) == (
        InsertStatus.INSERTED,
        InsertStatus.INSERTED,
    )
    assert (await repository.read(after_cursor=None, limit=10)).items == (first, other)
    await publisher.close()
    await database.close()


async def test_contentless_replay_and_read_limit_validation(tmp_path: Path) -> None:
    database, _service, publisher = await setup(tmp_path / "events.db")
    repository = SqliteObservationRepository(database)
    contentless = PreferenceStatementObservation(
        observation_id="obs_" + "f" * 32,
        idempotency_key="preference-1",
        occurred_at=NOW,
        received_at=NOW,
        account_id="account-1",
        content_ref=None,
        provenance=ObservationProvenance(
            producer_id="builtin.assistant",
            source=ObservationSource.ASSISTANT,
            authenticated=True,
            trust_level=TrustLevel.HIGH,
        ),
        payload={"statement": "Prefer science videos"},
    )
    assert (await repository.insert_batch((contentless,)))[0].status is InsertStatus.INSERTED
    assert (await repository.read(after_cursor=None, limit=10)).items == (contentless,)
    for limit in (0, 501):
        with pytest.raises(ValueError, match="limit"):
            await repository.read(after_cursor=None, limit=limit)
    await publisher.close()
    await database.close()
