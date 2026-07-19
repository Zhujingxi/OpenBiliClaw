"""Pool-share fairness: share-aware content-cache admission (Phase 2).

Spec: docs/plans/2026-07-20-pool-share-fairness-spec.md (Phase 2, invariant 3).

``_admit_until_full`` used to admit the FIFO ``evaluated`` queue purely against
the *global* pool cap, so a source with a huge backlog (reddit 169/25) grabbed
every freed slot and under-share sources never got in even once they had
supply. When a share-target strategy is injected, admission runs two rounds:
round 1 admits only under-share rows (family available < target), round 2 fills
any remaining global slots with the deferred over-share rows (availability
fallback). Without a strategy the behavior is byte-identical to before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class _CachingEngine:
    """Minimal discovery-engine stub whose cache admission always succeeds."""

    def cache_evaluated_results(self, items: list[DiscoveredContent]) -> int:
        return len(items)


class _FakeAdmissionDB:
    def __init__(self, *, available: dict[str, int], pool_count: int) -> None:
        self._available = dict(available)
        self.pool_count = pool_count
        self.rejected: list[tuple[int, str]] = []
        self.cached_ids: list[int] = []

    def count_pool_candidates(self, *, xhs_self_nickname: str = "") -> int:
        return self.pool_count

    def count_pool_available_candidates_by_source(
        self, *, max_per_topic_group: int = 3, xhs_self_nickname: str = ""
    ) -> dict[str, int]:
        return dict(self._available)

    def reject_discovery_candidate(self, candidate_id: int, *, status: str, reason: str) -> None:
        self.rejected.append((candidate_id, status))

    def mark_discovery_candidate_cached(self, candidate_id: int) -> None:
        self.cached_ids.append(candidate_id)
        self.pool_count += 1


def _row(candidate_id: int, *, platform: str, strategy: str) -> dict[str, Any]:
    return {"id": candidate_id, "source_platform": platform, "source_strategy": strategy}


def _item(candidate_id: int, *, platform: str, strategy: str) -> DiscoveredContent:
    return DiscoveredContent(
        content_id=f"{platform}-{candidate_id}",
        bvid=f"{platform}-{candidate_id}",
        title=f"item {candidate_id}",
        source_platform=platform,
        source_strategy=strategy,
        relevance_score=0.9,
    )


def _accepted(
    specs: list[tuple[int, str, str]],
) -> list[tuple[dict[str, Any], DiscoveredContent]]:
    return [
        (
            _row(cid, platform=platform, strategy=strategy),
            _item(cid, platform=platform, strategy=strategy),
        )
        for cid, platform, strategy in specs
    ]


def _pipeline(
    db: _FakeAdmissionDB, *, share_targets: dict[str, int] | None
) -> DiscoveryCandidatePipeline:
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=_CachingEngine(),  # type: ignore[arg-type]
        pool_target_count=300,
    )
    if share_targets is not None:
        pipeline.source_share_targets = lambda: dict(share_targets)
    return pipeline


def test_pool_full_for_source_is_false_for_under_share_family_when_global_full() -> None:
    # Global pool full, bangumi 0/50 under its own share → NOT full for bangumi
    # (two-round admission + rebalance will free a slot for it). This is the
    # producer-internal gate fix (spec 2026-07-20, Phase 5 / D6).
    db = _FakeAdmissionDB(available={"reddit": 169, "bangumi": 0}, pool_count=300)
    pipeline = _pipeline(db, share_targets={"reddit": 25, "bangumi": 50})

    assert pipeline.pool_full_for_source("bangumi") is False


def test_pool_full_for_source_is_true_for_over_share_family_when_global_full() -> None:
    db = _FakeAdmissionDB(available={"reddit": 169, "bangumi": 0}, pool_count=300)
    pipeline = _pipeline(db, share_targets={"reddit": 25, "bangumi": 50})

    assert pipeline.pool_full_for_source("reddit") is True


def test_pool_full_for_source_without_strategy_equals_global_pool_full() -> None:
    full = _FakeAdmissionDB(available={"bangumi": 0}, pool_count=300)
    pipeline_full = _pipeline(full, share_targets=None)
    assert pipeline_full.pool_full_for_source("bangumi") is True
    assert pipeline_full.pool_full_for_source("bangumi") == pipeline_full.pool_full()

    not_full = _FakeAdmissionDB(available={"bangumi": 0}, pool_count=250)
    pipeline_open = _pipeline(not_full, share_targets=None)
    assert pipeline_open.pool_full_for_source("bangumi") is False


def test_pool_full_for_source_is_false_whenever_global_pool_below_target() -> None:
    # Below target: never full for anyone, share strategy or not.
    db = _FakeAdmissionDB(available={"reddit": 169, "bangumi": 0}, pool_count=250)
    pipeline = _pipeline(db, share_targets={"reddit": 25, "bangumi": 50})

    assert pipeline.pool_full_for_source("reddit") is False
    assert pipeline.pool_full_for_source("bangumi") is False


def test_admission_prefers_under_share_rows_over_queue_position() -> None:
    # Queue: 5 over-share reddit rows AHEAD of 2 under-share bangumi rows.
    # Only 2 global slots (298/300). Share-aware admission must admit the 2
    # bangumi rows, never the reddit backlog.
    db = _FakeAdmissionDB(available={"reddit": 169, "bangumi": 0}, pool_count=298)
    pipeline = _pipeline(db, share_targets={"reddit": 25, "bangumi": 50})
    admitted: list[DiscoveredContent] = []

    accepted = _accepted(
        [(i, "reddit", "reddit") for i in range(5)]
        + [(100, "bangumi", "bangumi"), (101, "bangumi", "bangumi")]
    )
    cached, rejected = pipeline._admit_until_full(
        accepted, recently_viewed=set(), admitted_items=admitted, limit=None
    )

    assert cached == 2
    assert rejected == 0
    assert db.cached_ids == [100, 101]


def test_admission_falls_back_to_over_share_when_no_under_share_supply() -> None:
    # No under-share supply present; global pool has 3 free slots. The
    # over-share rows fill them (availability fallback beats purity).
    db = _FakeAdmissionDB(available={"reddit": 169}, pool_count=297)
    pipeline = _pipeline(db, share_targets={"reddit": 25})
    admitted: list[DiscoveredContent] = []

    accepted = _accepted([(i, "reddit", "reddit") for i in range(5)])
    cached, rejected = pipeline._admit_until_full(
        accepted, recently_viewed=set(), admitted_items=admitted, limit=None
    )

    assert cached == 3
    assert db.cached_ids == [0, 1, 2]


def test_admission_without_share_strategy_keeps_fifo_order() -> None:
    # Same fixture, no strategy injected → legacy behavior: admit the first 2
    # queue rows (the reddit backlog), byte-identical to before.
    db = _FakeAdmissionDB(available={"reddit": 169, "bangumi": 0}, pool_count=298)
    pipeline = _pipeline(db, share_targets=None)
    admitted: list[DiscoveredContent] = []

    accepted = _accepted(
        [(i, "reddit", "reddit") for i in range(5)]
        + [(100, "bangumi", "bangumi"), (101, "bangumi", "bangumi")]
    )
    cached, rejected = pipeline._admit_until_full(
        accepted, recently_viewed=set(), admitted_items=admitted, limit=None
    )

    assert cached == 2
    assert db.cached_ids == [0, 1]


def test_bilibili_strategies_resolve_to_one_family() -> None:
    db = _FakeAdmissionDB(available={}, pool_count=0)
    pipeline = _pipeline(db, share_targets={"bilibili": 5})
    for strategy in ("search", "related_chain", "trending", "explore"):
        assert (
            pipeline._row_source_family(
                _row(1, platform="bilibili", strategy=strategy),
                _item(1, platform="bilibili", strategy=strategy),
            )
            == "bilibili"
        )
    assert (
        pipeline._row_source_family(
            _row(1, platform="reddit", strategy="reddit"),
            _item(1, platform="reddit", strategy="reddit"),
        )
        == "reddit"
    )


def _seed_evaluated(db: Database, *, key: str, platform: str, strategy: str, order: int) -> None:
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=key,
                source_platform=platform,
                source_strategy=strategy,
                content_id=key,
                title=key,
            )
        ]
    )
    # Deterministic evaluated_at so FIFO ordering is testable across platforms.
    db.conn.execute(
        """
        UPDATE discovery_candidates
        SET status = 'evaluated',
            relevance_score = 0.9,
            pool_expression = 'x',
            pool_topic_label = 'x',
            style_key = 'deep_dive',
            topic_group = 'tech',
            evaluated_at = datetime('2026-07-20 00:00:00', '+' || ? || ' seconds')
        WHERE candidate_key = ?
        """,
        (order, key),
    )
    db.conn.commit()


def test_admission_query_orders_preferred_platforms_ahead_of_fifo(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    # reddit rows are OLDER (earlier evaluated_at) than the bangumi row.
    _seed_evaluated(db, key="reddit:A", platform="reddit", strategy="reddit", order=0)
    _seed_evaluated(db, key="reddit:B", platform="reddit", strategy="reddit", order=1)
    _seed_evaluated(db, key="bangumi:C", platform="bangumi", strategy="bangumi", order=2)

    preferred = db.get_evaluated_discovery_candidates_for_admission(
        limit=10, preferred_source_platforms=["bangumi"]
    )
    assert [row["candidate_key"] for row in preferred] == ["bangumi:C", "reddit:A", "reddit:B"]

    # Default (no preference) keeps the legacy FIFO evaluated_at ordering.
    default = db.get_evaluated_discovery_candidates_for_admission(limit=10)
    assert [row["candidate_key"] for row in default] == ["reddit:A", "reddit:B", "bangumi:C"]
