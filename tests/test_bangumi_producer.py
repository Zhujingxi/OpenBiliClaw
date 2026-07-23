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
    def __init__(self, *, full: bool = False, under_share_families: tuple[str, ...] = ()) -> None:
        self.full = full
        self.under_share_families = set(under_share_families)
        self.enqueued: list[tuple[list[Any], str]] = []
        self.drains: list[int] = []

    def pool_full(self) -> bool:
        return self.full

    def pool_full_for_source(self, source_family: str) -> bool:
        # Mirror the real pipeline's share-aware gate: an under-share family is
        # not "full" even when the global pool is at target.
        if not self.full:
            return False
        return source_family not in self.under_share_families

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
async def test_producer_runs_when_global_full_but_bangumi_under_share(db: Database) -> None:
    # Pool-share fairness (spec 2026-07-20, Phase 5 / D6): the producer-internal
    # global pool_full gate must defer to the share-aware gate. Global pool is
    # full but bangumi is below its own share, so the producer must NOT skip
    # with pool_full — it proceeds to produce (which unblocks the rebalance
    # dead-lock: no supply → no eviction → pool stays full forever).
    under_share = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        enabled=True,
        candidate_pipeline=_Pipeline(full=True, under_share_families=("bangumi",)),
    )
    result = await under_share.produce_if_due()
    assert result.get("reason") != "pool_full"


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
async def test_unauthorized_degrades_token_and_keeps_working(db: Database) -> None:
    """A 401 on a token-bearing discovery client drops the token, not the run."""

    class _TokenClient(_Client):
        def __init__(self) -> None:
            self._token: str | None = "tok"
            self.saw_token_on_search = True

        @property
        def has_access_token(self) -> bool:
            return self._token is not None

        def disable_access_token(self) -> None:
            self._token = None

        async def search_subjects(self, keyword: str, **kwargs: Any) -> BangumiPage:
            # The token expired: the very first authenticated call is rejected.
            if self._token is not None:
                self.saw_token_on_search = True
                raise BangumiAPIError("unauthorized", "denied", status_code=401)
            return await super().search_subjects(keyword, **kwargs)

    client = _TokenClient()
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        access_token="tok",
        enabled=True,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=6)

    # Search reported the unauthorized error but the browse modes still produced
    # candidates anonymously, and the token was dropped from client + producer.
    assert result["mode_results"]["search"] == "unauthorized"
    assert result["discovered"] > 0
    assert client.has_access_token is False
    assert producer.access_token == ""


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


class _AuthTrackingClient(_Client):
    """A token-bearing client that records whether it made authenticated calls.

    ``reject`` simulates an expired token that 401s on the first authed request;
    otherwise authed requests succeed like the base client.
    """

    def __init__(self, token: str = "tok", *, reject: bool = False) -> None:
        self._token: str | None = token or None
        self.reject = reject
        self.authed_calls = 0

    @property
    def has_access_token(self) -> bool:
        return self._token is not None

    def disable_access_token(self) -> None:
        self._token = None

    async def search_subjects(self, keyword: str, **kwargs: Any) -> BangumiPage:
        if self._token is not None:
            self.authed_calls += 1
            if self.reject:
                raise BangumiAPIError("unauthorized", "denied", status_code=401)
        return await super().search_subjects(keyword, **kwargs)

    async def browse_subjects(self, subject_type: str, *, sort: str, **kwargs: Any) -> BangumiPage:
        if self._token is not None:
            self.authed_calls += 1
            if self.reject:
                raise BangumiAPIError("unauthorized", "denied", status_code=401)
        return await super().browse_subjects(subject_type, sort=sort, **kwargs)


@pytest.mark.asyncio
async def test_unauthorized_persists_token_rejection_marker(db: Database) -> None:
    """A 401 records a durable fingerprint-keyed marker (never the token)."""

    client = _AuthTrackingClient(token="tok", reject=True)
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        access_token="tok",
        enabled=True,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=6)

    marker = bangumi_producer_module._read_token_rejection(db)
    assert marker is not None
    assert marker["fingerprint"] == bangumi_producer_module._token_fingerprint("tok")
    # The token itself is never persisted anywhere in the marker.
    assert "tok" not in str(marker)
    assert result["discovered"] > 0
    assert producer.access_token == ""
    assert client.has_access_token is False


@pytest.mark.asyncio
async def test_persisted_rejection_starts_anonymous_without_bearer(db: Database) -> None:
    """A restart with the same rejected token must not re-send the Bearer."""

    # First cycle: a rejecting client persists the marker and creates the table.
    seeder = _AuthTrackingClient(token="tok", reject=True)
    BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=seeder,
        access_token="tok",
        enabled=True,
        min_interval_minutes=0,
    )
    await BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=seeder,
        access_token="tok",
        enabled=True,
        min_interval_minutes=0,
    ).produce_if_due(limit=6)
    assert bangumi_producer_module._read_token_rejection(db) is not None

    # Simulated restart: a fresh producer with the same (unchanged) token starts
    # anonymous — never carrying the Bearer — so it cannot eat another 401.
    fresh_client = _AuthTrackingClient(token="tok", reject=False)
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=fresh_client,
        access_token="tok",
        enabled=True,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=6, force=True)

    assert fresh_client.authed_calls == 0
    assert fresh_client.has_access_token is False
    assert result["discovered"] > 0
    # The marker persists (the unchanged token was never re-validated).
    assert bangumi_producer_module._read_token_rejection(db) is not None


@pytest.mark.asyncio
async def test_rotated_token_success_clears_marker(db: Database) -> None:
    """A new token that works clears the stale marker so the account re-arms."""

    client = _AuthTrackingClient(token="newtok", reject=False)
    producer = BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=client,
        access_token="newtok",
        enabled=True,
        min_interval_minutes=0,
    )
    producer._ensure_tables()
    # Marker left by a *different* (old) token — the user has since rotated it.
    bangumi_producer_module._persist_token_rejection(
        db, bangumi_producer_module._token_fingerprint("oldtok")
    )

    result = await producer.produce_if_due(limit=6, force=True)

    assert client.authed_calls > 0
    assert client.has_access_token is True
    assert result["discovered"] > 0
    assert bangumi_producer_module._read_token_rejection(db) is None


def test_source_status_reports_token_state(db: Database) -> None:
    # No token configured: the dimension is omitted (historical shape).
    status = bangumi_source_status(db, enabled=True, token_configured=False)
    assert "token_state" not in status

    # Token configured, no marker: ok, and detail drops "无需登录".
    status = bangumi_source_status(db, enabled=True, token_configured=True)
    assert status["token_state"] == "ok"
    assert "无需登录" not in str(status["detail"])

    # A persisted rejection marker surfaces the actionable warning.
    BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        enabled=True,
    )._ensure_tables()
    bangumi_producer_module._persist_token_rejection(
        db, bangumi_producer_module._token_fingerprint("tok")
    )
    status = bangumi_source_status(db, enabled=True, token_configured=True)
    assert status["token_state"] == "rejected"
    assert "已被拒绝" in str(status["detail"])


def test_disabled_source_status_names_the_saved_credential(db: Database) -> None:
    """Saving a token and forgetting the switch must not read as "nothing here"."""

    # No credential at all: the historical wording is what the user should see.
    bare = bangumi_source_status(db, enabled=False)
    assert bare["state"] == "disabled"
    assert bare["detail"] == "Bangumi 来源未启用。"
    assert "token_state" not in bare

    # A saved token is called out, and the structured dimension is filled in so
    # the front ends do not have to infer it from prose.
    with_token = bangumi_source_status(db, enabled=False, token_configured=True)
    assert with_token["state"] == "disabled"
    assert with_token["token_state"] == "ok"
    assert "已保存个人令牌" in str(with_token["detail"])
    assert "「启用」" in str(with_token["detail"])
    # Both renderers prefix the detail with their own "来源未启用" label (and the
    # popup adds "(未启用)" as well), so the detail must not say it a third time.
    assert "未启用" not in str(with_token["detail"])

    # A public username alone is a credential too, but it is not a token.
    with_username = bangumi_source_status(db, enabled=False, username_configured=True)
    assert "已保存公开用户名" in str(with_username["detail"])
    assert "未启用" not in str(with_username["detail"])
    assert "token_state" not in with_username

    # The token wins when both are set — the account comes from /v0/me.
    both = bangumi_source_status(db, enabled=False, token_configured=True, username_configured=True)
    assert "已保存个人令牌" in str(both["detail"])

    # A dead token stays visible while disabled, and the wording must not claim
    # that anonymous discovery is currently running (nothing runs while off).
    BangumiDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        client=_Client(),
        enabled=False,
    )._ensure_tables()
    bangumi_producer_module._persist_token_rejection(
        db, bangumi_producer_module._token_fingerprint("tok")
    )
    rejected = bangumi_source_status(db, enabled=False, token_configured=True)
    assert rejected["state"] == "disabled"
    assert rejected["token_state"] == "rejected"
    assert "拒绝" in str(rejected["detail"])
    assert "已降级为匿名公开发现" not in str(rejected["detail"])


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
