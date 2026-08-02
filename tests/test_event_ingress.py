"""Durable event-ingress acceptance and migration regressions."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.runtime.event_ingress import EventIngressService
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def test_legacy_event_table_adds_ingest_key_before_partial_unique_index(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            url TEXT,
            title TEXT,
            context TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO events (event_type, title, metadata) VALUES ('view', 'old-1', '{}');
        INSERT INTO events (event_type, title, metadata) VALUES ('view', 'old-2', '{}');
        """
    )
    legacy.commit()
    legacy.close()

    database = Database(path)
    database.initialize()

    columns = {str(row["name"]): row for row in database.conn.execute("PRAGMA table_info(events)")}
    indexes = {str(row["name"]): row for row in database.conn.execute("PRAGMA index_list(events)")}
    assert columns["ingest_key"]["notnull"] == 1
    assert columns["ingest_key"]["dflt_value"] == "''"
    assert indexes["idx_events_ingest_key_unique"]["unique"] == 1
    assert indexes["idx_events_ingest_key_unique"]["partial"] == 1

    first = database.insert_events_with_receipts(
        [{"event_type": "click", "title": "first write", "ingest_key": "api:req-1"}]
    )[0]
    replay = database.insert_events_with_receipts(
        [{"event_type": "click", "title": "changed replay", "ingest_key": "api:req-1"}]
    )[0]
    database.insert_events_with_receipts(
        [
            {"event_type": "view", "title": "legacy append 1"},
            {"event_type": "view", "title": "legacy append 2"},
        ]
    )

    assert first.inserted is True
    assert replay.inserted is False
    assert replay.event_id == first.event_id
    assert database.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 5
    database.close()


def test_concurrent_duplicate_receipts_return_one_stable_first_write(tmp_path: Path) -> None:
    database = Database(tmp_path / "concurrent.db")
    database.initialize()

    def submit(index: int) -> tuple[int, bool]:
        result = database.insert_events_with_receipts(
            [
                {
                    "event_type": "click",
                    "title": f"racing payload {index}",
                    "metadata": {},
                    "ingest_key": "extension:shared-request",
                }
            ]
        )[0]
        return result.event_id, result.inserted

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(submit, range(16)))

    assert len({event_id for event_id, _inserted in results}) == 1
    assert sum(1 for _event_id, inserted in results if inserted) == 1
    stored = database.conn.execute(
        "SELECT title FROM events WHERE ingest_key = ?",
        ("extension:shared-request",),
    ).fetchall()
    assert len(stored) == 1
    assert str(stored[0]["title"]).startswith("racing payload ")
    database.close()


@pytest.mark.asyncio
async def test_ingress_namespaces_idempotency_keys_by_producer(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    wakes = 0

    def wake() -> None:
        nonlocal wakes
        wakes += 1

    ingress = EventIngressService(memory, wake=wake)
    event = {
        "event_type": "click",
        "title": "same client id",
        "metadata": {},
        "ingest_key": "request-7",
    }

    extension_first = await ingress.accept(event, producer="extension")
    extension_replay = await ingress.accept(event, producer="extension")
    web_first = await ingress.accept(event, producer="web")

    assert extension_first.inserted == 1
    assert extension_replay.duplicates == 1
    assert extension_replay.items[0].event_id == extension_first.items[0].event_id
    assert web_first.inserted == 1
    assert web_first.items[0].event_id != extension_first.items[0].event_id
    assert wakes == 3
    rows = memory.query_event_rows_after(after_event_id=0)
    assert [row["ingest_key"] for row in rows] == [
        "extension:request-7",
        "web:request-7",
    ]


@pytest.mark.asyncio
async def test_ingress_partially_accepts_valid_rows_and_wakes_once(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    prepared = 0
    wakes = 0

    async def prepare() -> None:
        nonlocal prepared
        prepared += 1

    def wake() -> None:
        nonlocal wakes
        wakes += 1

    ingress = EventIngressService(memory, prepare_owner=prepare, wake=wake)
    receipt = await ingress.accept_batch(
        [
            {
                "event_type": "click",
                "title": "accepted",
                "metadata": {},
                "ingest_key": "mixed-1",
            },
            {"event_type": "unsupported", "metadata": {}, "ingest_key": "mixed-2"},
            {"event_type": "view", "metadata": "not-an-object", "ingest_key": "mixed-3"},
        ],
        producer="extension",
    )

    assert receipt.accepted == 1
    assert receipt.inserted == 1
    assert receipt.rejected == 2
    assert [item.index for item in receipt.items] == [0, 1, 2]
    assert receipt.items[0].event_id > 0
    assert "Unsupported event type" in receipt.items[1].error
    assert "metadata must be an object" in receipt.items[2].error
    assert prepared == 1
    assert wakes == 1
    assert [row["title"] for row in memory.query_event_rows_after(after_event_id=0)] == ["accepted"]


@pytest.mark.asyncio
async def test_post_commit_wake_failure_still_returns_durable_receipt(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    def fail_wake() -> None:
        raise RuntimeError("scheduler unavailable")

    ingress = EventIngressService(memory, wake=fail_wake)
    receipt = await ingress.accept(
        {
            "event_type": "click",
            "title": "wake is only a hint",
            "metadata": {},
            "ingest_key": "wake-failure-1",
        },
        producer="extension",
    )

    assert receipt.accepted == 1
    assert receipt.inserted == 1
    assert receipt.items[0].event_id > 0
    [row] = memory.query_event_rows_after(after_event_id=0)
    assert row["title"] == "wake is only a hint"
