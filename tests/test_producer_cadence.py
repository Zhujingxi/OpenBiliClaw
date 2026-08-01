"""Guards for the shared, restart-surviving producer cadence floor.

The floor only works if the producer actually holds a database handle. Zhihu
shipped without one: it reaches storage through ``ZhihuTaskQueue(database)``,
so the missing ``database`` field looked harmless and silently downgraded the
producer back to the in-process stamp this module exists to replace. Nothing
failed loudly — a live backend just kept bypassing its own floor on restart.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.runtime.douyin_producer import DouyinDiscoveryProducer
from openbiliclaw.runtime.producer_cadence import (
    ledger_available,
    producer_ran_within,
    record_producer_run,
)
from openbiliclaw.runtime.reddit_producer import RedditDiscoveryProducer
from openbiliclaw.runtime.x_producer import XDiscoveryProducer
from openbiliclaw.runtime.youtube_producer import YoutubeDiscoveryProducer
from openbiliclaw.runtime.zhihu_producer import ZhihuDiscoveryProducer
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

# Every producer whose floor moved off the in-process attribute.
LEDGER_BACKED_PRODUCERS = (
    DouyinDiscoveryProducer,
    YoutubeDiscoveryProducer,
    XDiscoveryProducer,
    ZhihuDiscoveryProducer,
    RedditDiscoveryProducer,
)


@pytest.mark.parametrize("producer_cls", LEDGER_BACKED_PRODUCERS, ids=lambda c: c.__name__)
def test_ledger_backed_producers_accept_a_database(producer_cls: type) -> None:
    """Without this field the producer silently falls back to the old behaviour."""
    fields = {f.name for f in dataclasses.fields(producer_cls)}
    assert "database" in fields, (
        f"{producer_cls.__name__} cannot reach the cadence ledger, so its floor "
        "will not survive a restart"
    )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(str(tmp_path / "cadence.db"))
    database.initialize()
    return database


def test_only_productive_rounds_are_recorded(db: Database) -> None:
    db.record_source_producer_run("douyin", 0)
    assert db.source_producer_ran_within("douyin", 60) is False

    db.record_source_producer_run("douyin", 3)
    assert db.source_producer_ran_within("douyin", 60) is True


def test_ledger_is_per_platform(db: Database) -> None:
    db.record_source_producer_run("douyin", 3)

    assert db.source_producer_ran_within("youtube", 60) is False
    assert db.source_producer_ran_within("  DouYin  ", 60) is True


def test_zero_interval_never_throttles(db: Database) -> None:
    db.record_source_producer_run("reddit", 9)

    assert db.source_producer_ran_within("reddit", 0) is False


def test_helpers_degrade_without_a_database() -> None:
    """Producers built without storage keep working on the in-process stamp."""
    assert ledger_available(None) is False
    assert producer_ran_within(None, "douyin", 10) is False
    record_producer_run(None, "douyin", 5)  # must not raise


def test_lookup_failure_is_treated_as_due() -> None:
    """A broken read must not be able to pin a producer shut."""

    class Exploding:
        def record_source_producer_run(self, platform: str, discovered: int) -> None:
            raise RuntimeError("boom")

        def source_producer_ran_within(self, platform: str, minutes: int) -> bool:
            raise RuntimeError("boom")

    broken = Exploding()
    assert ledger_available(broken) is True
    assert producer_ran_within(broken, "douyin", 10) is False
    record_producer_run(broken, "douyin", 5)  # swallowed, not raised
