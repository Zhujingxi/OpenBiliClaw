from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

import openbiliclaw.runtime.bangumi_producer as bangumi_producer_module
from openbiliclaw.runtime.bangumi_producer import (
    BangumiDiscoveryProducer,
    bangumi_source_status,
)
from openbiliclaw.runtime.keyword_fetch import ClaimedKeyword
from openbiliclaw.sources.bangumi import bangumi_subject_to_content
from openbiliclaw.sources.bangumi_client import BangumiAPIError, BangumiPage
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "bangumi.db")
    database.initialize()
    return database


class _Soul:
    async def get_profile(self) -> dict[str, Any]:
        return {"preferences": {"interests": [{"name": "科幻"}]}}


@dataclass
class _Client:
    fail_mode: str = ""

    async def search_subjects(self, keyword: str, **kwargs: Any) -> BangumiPage:
        if self.fail_mode == "search":
            raise BangumiAPIError("schema_changed", "changed")
        return BangumiPage([_subject(1, f"{keyword}动画")], 1, 1, 0)

    async def browse_subjects(self, subject_type: str, *, sort: str, **kwargs: Any) -> BangumiPage:
        mode = "ranked" if sort == "rank" else "latest"
        if self.fail_mode == mode:
            raise BangumiAPIError("upstream_error", "down")
        base = {"anime": 10, "book": 20, "game": 30}.get(subject_type, 40)
        return BangumiPage([_subject(base + (0 if sort == "rank" else 1), subject_type)], 100, 1, 0)


def _subject(subject_id: int, title: str) -> dict[str, object]:
    return {
        "id": subject_id,
        "type": 2,
        "name": title,
        "name_cn": title,
        "nsfw": False,
        "rating": {"score": 8.5, "total": 100, "rank": subject_id},
    }


class _Pipeline:
    def __init__(self, *, full: bool = False) -> None:
        self.full = full
        self.enqueued: list[tuple[list[Any], str]] = []
        self.drains: list[int] = []

    def pool_full(self) -> bool:
        return self.full

    def enqueue_candidates(self, items: list[Any], *, source_context: str) -> int:
        self.enqueued.append((items, source_context))
        return len(items)

    async def drain_pending(self, *, profile: Any, batch_size: int) -> dict[str, int]:
        self.drains.append(batch_size)
        return {"cached": 1}


class _Keywords:
    def __init__(self) -> None:
        self.used: list[int] = []
        self.failed: list[int] = []
        self.rolled_back: list[int] = []

    def should_claim(self) -> bool:
        return True

    def claim(self, platform: str, n: int | None = None) -> list[ClaimedKeyword]:
        assert platform == "bangumi"
        return [ClaimedKeyword(id=7, keyword="机甲")]

    def mark_used(self, claimed: list[ClaimedKeyword]) -> None:
        self.used.extend(item.id for item in claimed)

    def mark_failed(self, claimed: list[ClaimedKeyword]) -> None:
        self.failed.extend(item.id for item in claimed)

    def rollback(self, claimed: ClaimedKeyword) -> None:
        self.rolled_back.append(claimed.id)


@pytest.mark.asyncio
async def test_producer_runs_three_modes_and_enqueues(db: Database) -> None:
    pipeline = _Pipeline()
    keywords = _Keywords()
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        enabled=True,
        subject_types=("anime", "book", "game"),
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=9)

    assert result["reason"] == "ok"
    assert result["discovered"] == 7
    assert result["enqueued"] == 7
    assert keywords.used == [7]
    assert {context for _, context in pipeline.enqueued} == {
        "bangumi-search",
        "bangumi-ranked",
        "bangumi-latest",
    }
    assert pipeline.drains == [9]
    assert producer.consumed_today("search") == 1
    assert producer.consumed_today("ranked") == 3


@pytest.mark.asyncio
async def test_producer_partial_failure_keeps_other_modes(db: Database) -> None:
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(fail_mode="search"),
        enabled=True,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=6)

    assert result["reason"] == "partial"
    assert result["discovered"] > 0
    assert result["mode_results"]["search"] == "schema_changed"


@pytest.mark.asyncio
async def test_browse_invalid_cursor_resets_and_retries_once(db: Database) -> None:
    class _CursorClient(_Client):
        def __init__(self) -> None:
            self.offsets: list[int] = []

        async def browse_subjects(
            self, subject_type: str, *, sort: str, **kwargs: Any
        ) -> BangumiPage:
            assert subject_type == "anime"
            assert sort == "rank"
            offset = int(kwargs["offset"])
            self.offsets.append(offset)
            if offset > 0:
                raise BangumiAPIError("invalid_request", "offset out of range")
            return BangumiPage([_subject(10, "anime")], 100, 1, 0)

    client = _CursorClient()
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        subject_types=("anime",),
        source_modes=("ranked",),
        min_interval_minutes=0,
    )
    producer._ensure_tables()
    producer._set_cursor("ranked", "anime", 99_999, 100_000)

    result = await producer.produce_if_due(limit=1)

    assert result["reason"] == "ok"
    assert client.offsets == [99_999, 0]
    assert producer._cursor("ranked", "anime") == 1


@pytest.mark.asyncio
async def test_browse_rotates_subject_types_when_limit_is_smaller_than_type_count(
    db: Database,
) -> None:
    class _RotatingClient(_Client):
        def __init__(self) -> None:
            self.subject_types: list[str] = []

        async def browse_subjects(
            self, subject_type: str, *, sort: str, **kwargs: Any
        ) -> BangumiPage:
            assert sort == "rank"
            self.subject_types.append(subject_type)
            subject_id = {"anime": 10, "book": 20, "game": 30}[subject_type]
            return BangumiPage([_subject(subject_id, subject_type)], 100, 1, 0)

    client = _RotatingClient()
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        enabled=True,
        subject_types=("anime", "book", "game"),
        source_modes=("ranked",),
        min_interval_minutes=0,
    )

    results = [await producer.produce_if_due(limit=1) for _ in range(4)]

    assert [result["reason"] for result in results] == ["ok", "ok", "ok", "ok"]
    assert client.subject_types == ["anime", "book", "game", "anime"]


@pytest.mark.asyncio
async def test_search_failure_releases_unattempted_keyword_claims(db: Database) -> None:
    class _TwoKeywords(_Keywords):
        def claim(self, platform: str, n: int | None = None) -> list[ClaimedKeyword]:
            assert platform == "bangumi"
            return [
                ClaimedKeyword(id=7, keyword="机甲"),
                ClaimedKeyword(id=8, keyword="赛博朋克"),
            ]

    keywords = _TwoKeywords()
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(fail_mode="search"),
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=4)

    assert result["reason"] == "error"
    assert keywords.failed == [7]
    assert keywords.rolled_back == [8]


@pytest.mark.asyncio
async def test_empty_claimed_keyword_is_failed_instead_of_released(db: Database) -> None:
    class _EmptyKeyword(_Keywords):
        def claim(self, platform: str, n: int | None = None) -> list[ClaimedKeyword]:
            assert platform == "bangumi"
            return [ClaimedKeyword(id=7, keyword="   ")]

    keywords = _EmptyKeyword()
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=4)

    assert result["reason"] == "empty"
    assert keywords.failed == [7]
    assert keywords.rolled_back == []


@pytest.mark.asyncio
async def test_budget_counts_only_final_globally_deduplicated_items(db: Database) -> None:
    def content(subject_id: int, strategy: str) -> Any:
        item = bangumi_subject_to_content(
            _subject(subject_id, f"条目 {subject_id}"),
            strategy=strategy,
        )
        assert item is not None
        return item

    class _OverlappingProducer(BangumiDiscoveryProducer):
        async def _run_search(self, profile: Any, limit: int) -> list[Any]:
            del profile, limit
            return [content(1, "bangumi-search"), content(2, "bangumi-search")]

        async def _run_browse(self, mode: str, limit: int) -> list[Any]:
            del limit
            if mode == "ranked":
                return [content(2, "bangumi-ranked"), content(3, "bangumi-ranked")]
            return [content(4, "bangumi-latest")]

    producer = _OverlappingProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        enabled=True,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=3)

    assert result["discovered"] == 3
    assert result["source_counts"] == {
        "bangumi-search": 2,
        "bangumi-ranked": 1,
    }
    assert producer.consumed_today("search") == 2
    assert producer.consumed_today("ranked") == 1
    assert producer.consumed_today("latest") == 0
    ledger = {
        str(row["mode"]): (int(row["units"]), int(row["discovered"]))
        for row in db.conn.execute(
            "SELECT mode, units, discovered FROM bangumi_discovery_runs"
        ).fetchall()
    }
    assert ledger == {
        "search": (2, 2),
        "ranked": (1, 2),
        "latest": (0, 1),
    }


@pytest.mark.asyncio
async def test_all_modes_budget_exhausted_reports_budget_exhausted(db: Database) -> None:
    class _NoBudget(BangumiDiscoveryProducer):
        def remaining_budget(self, mode: str, *, per_run_budget: int) -> int:
            del mode, per_run_budget
            return 0

    producer = _NoBudget(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        enabled=True,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=6)

    assert result["reason"] == "budget_exhausted"
    assert result["discovered"] == 0
    assert result["mode_results"] == {
        "search": "budget_exhausted",
        "ranked": "budget_exhausted",
        "latest": "budget_exhausted",
    }


@pytest.mark.asyncio
async def test_partial_budget_exhaustion_still_reports_empty(db: Database) -> None:
    class _EmptyBrowse(_Client):
        async def browse_subjects(
            self, subject_type: str, *, sort: str, **kwargs: Any
        ) -> BangumiPage:
            return BangumiPage([], 0, 1, 0)

    class _SearchBudgetOnly(BangumiDiscoveryProducer):
        def remaining_budget(self, mode: str, *, per_run_budget: int) -> int:
            if mode == "search":
                return 0
            return super().remaining_budget(mode, per_run_budget=per_run_budget)

    producer = _SearchBudgetOnly(
        database=db,
        soul_engine=_Soul(),
        client=_EmptyBrowse(),
        enabled=True,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=6)

    # A genuine successful-but-empty fetch must not be mislabelled as exhausted.
    assert result["reason"] == "empty"
    assert result["discovered"] == 0
    assert result["mode_results"] == {
        "search": "budget_exhausted",
        "ranked": "empty",
        "latest": "empty",
    }


@pytest.mark.asyncio
async def test_producer_stops_when_disabled_or_pool_full(db: Database) -> None:
    disabled = BangumiDiscoveryProducer(
        database=db, soul_engine=_Soul(), client=_Client(), enabled=False
    )
    assert await disabled.produce_if_due() == {"discovered": 0, "reason": "disabled"}

    full = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        enabled=True,
        candidate_pipeline=_Pipeline(full=True),
    )
    assert await full.produce_if_due() == {"discovered": 0, "reason": "pool_full"}


@pytest.mark.asyncio
async def test_rate_limit_persists_cooldown(db: Database) -> None:
    class _RateLimited(_Client):
        async def search_subjects(self, keyword: str, **kwargs: Any) -> BangumiPage:
            raise BangumiAPIError("rate_limited", "slow", retry_after_seconds=60)

        async def browse_subjects(
            self, subject_type: str, *, sort: str, **kwargs: Any
        ) -> BangumiPage:
            raise BangumiAPIError("rate_limited", "slow", retry_after_seconds=60)

    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_RateLimited(),
        enabled=True,
        min_interval_minutes=0,
    )
    first = await producer.produce_if_due(limit=3)
    second = await producer.produce_if_due(limit=3)

    assert first["reason"] == "error"
    assert second == {"discovered": 0, "reason": "rate_limited"}
    assert bangumi_source_status(db, enabled=True)["state"] == "rate_limited"


@pytest.mark.asyncio
async def test_rate_limit_releases_current_and_unattempted_keywords(db: Database) -> None:
    class _TwoKeywords(_Keywords):
        def claim(self, platform: str, n: int | None = None) -> list[ClaimedKeyword]:
            assert platform == "bangumi"
            return [
                ClaimedKeyword(id=7, keyword="机甲"),
                ClaimedKeyword(id=8, keyword="赛博朋克"),
            ]

    class _RateLimited(_Client):
        async def search_subjects(self, keyword: str, **kwargs: Any) -> BangumiPage:
            raise BangumiAPIError("rate_limited", "slow", retry_after_seconds=60)

    keywords = _TwoKeywords()
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_RateLimited(),
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=4)

    assert result["reason"] == "error"
    assert keywords.failed == []
    assert keywords.rolled_back == [7, 8]


@pytest.mark.asyncio
async def test_rate_limit_after_first_keyword_keeps_completed_candidates(db: Database) -> None:
    class _TwoKeywords(_Keywords):
        def claim(self, platform: str, n: int | None = None) -> list[ClaimedKeyword]:
            assert platform == "bangumi"
            return [
                ClaimedKeyword(id=7, keyword="机甲"),
                ClaimedKeyword(id=8, keyword="赛博朋克"),
            ]

    class _RateLimitedSecond(_Client):
        def __init__(self) -> None:
            self.calls = 0

        async def search_subjects(self, keyword: str, **kwargs: Any) -> BangumiPage:
            self.calls += 1
            if self.calls == 2:
                raise BangumiAPIError("rate_limited", "slow", retry_after_seconds=60)
            return BangumiPage([_subject(1, f"{keyword}动画")], 1, 1, 0)

    keywords = _TwoKeywords()
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_RateLimitedSecond(),
        enabled=True,
        source_modes=("search",),
        min_interval_minutes=0,
        keyword_fetch=keywords,
    )

    result = await producer.produce_if_due(limit=4)

    assert result["reason"] == "partial"
    assert result["discovered"] == 1
    assert result["mode_results"] == {"search": "rate_limited"}
    assert keywords.used == [7]
    assert keywords.failed == []
    assert keywords.rolled_back == [8]
    assert producer.consumed_today("search") == 1
    ledger = db.conn.execute(
        "SELECT units, discovered, reason, error_code FROM bangumi_discovery_runs"
    ).fetchone()
    assert ledger is not None
    assert tuple(ledger) == (1, 1, "partial", "rate_limited")


def test_source_status_is_local_and_reports_last_runs(db: Database) -> None:
    assert bangumi_source_status(db, enabled=False)["state"] == "disabled"
    assert bangumi_source_status(db, enabled=True)["state"] == "unverified"

    db.conn.execute(
        "INSERT INTO bangumi_discovery_runs(mode, units, discovered, reason) "
        "VALUES ('search', 1, 1, 'ok')"
    )
    db.conn.commit()

    status = bangumi_source_status(db, enabled=True)
    assert status["state"] == "ready"
    assert status["modes"]["search"]["reason"] == "ok"


def test_source_status_does_not_construct_a_producer(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _UnexpectedProducer:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError(f"status path constructed producer with {kwargs}")

    monkeypatch.setattr(
        bangumi_producer_module,
        "BangumiDiscoveryProducer",
        _UnexpectedProducer,
    )

    status = bangumi_source_status(db, enabled=True)

    assert status["state"] == "unverified"
