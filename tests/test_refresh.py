"""Tests for ContinuousRefreshController.run_init_backfill (gui-init plan B1)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from openbiliclaw.runtime.refresh import (
    ContinuousRefreshController,
    InitialPoolUnavailableError,
)
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile


class _FakeDisc:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.lock: asyncio.Lock | None = None
        self.locked_during: bool | None = None

    async def discover(
        self,
        profile: Any,
        *,
        strategies: Any,
        limit: int,
        fully_parallel: bool,
        pool_snapshot: Any | None = None,
    ) -> list[str]:
        self.calls.append(
            {
                "strategies": strategies,
                "limit": limit,
                "fully_parallel": fully_parallel,
                "pool_snapshot": pool_snapshot,
            }
        )
        if self.lock is not None:
            self.locked_during = self.lock.locked()
        return ["a", "b"]


class _FakeDB:
    def __init__(self, counts: list[int]) -> None:
        self._counts = list(counts)

    def count_pool_candidates(self, **_kw: Any) -> int:
        return self._counts.pop(0) if self._counts else 999


def _ctrl(db: Any, disc: Any) -> ContinuousRefreshController:
    return ContinuousRefreshController(
        memory_manager=SimpleNamespace(),
        database=db,
        soul_engine=SimpleNamespace(),
        discovery_engine=disc,
        recommendation_engine=SimpleNamespace(),
    )


async def test_run_init_backfill_discovers_with_expected_shape() -> None:
    disc = _FakeDisc()
    ctrl = _ctrl(_FakeDB([0]), disc)
    n = await ctrl.run_init_backfill(object(), target_pool_count=15)
    assert n == 2
    assert disc.calls == [
        {
            "strategies": ["search", "trending", "related_chain", "explore"],
            "limit": 20,  # max(20, target - current)
            "fully_parallel": True,
            "pool_snapshot": None,
        }
    ]


async def test_run_init_backfill_passes_cold_start_snapshot_for_empty_pool() -> None:
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name="人工智能", category="科技", weight=0.95),
                InterestTag(name="篮球战术", category="体育", weight=0.72),
                InterestTag(name="电影拉片", category="影视", weight=0.68),
            ]
        )
    )
    disc = _FakeDisc()
    ctrl = _ctrl(_FakeDB([0]), disc)

    await ctrl.run_init_backfill(profile, target_pool_count=15)

    snapshot = disc.calls[0]["pool_snapshot"]
    assert snapshot is not None
    assert snapshot.cold_start is True
    assert "人工智能" in snapshot.saturated_topics
    assert "篮球战术" in snapshot.undercovered_axes


async def test_run_init_backfill_skips_when_pool_already_full() -> None:
    disc = _FakeDisc()
    ctrl = _ctrl(_FakeDB([50]), disc)  # already above target
    n = await ctrl.run_init_backfill(object(), target_pool_count=15)
    assert n == 0
    assert disc.calls == []


async def test_run_init_backfill_holds_refresh_lock() -> None:
    disc = _FakeDisc()
    ctrl = _ctrl(_FakeDB([0]), disc)
    disc.lock = ctrl._refresh_lock
    await ctrl.run_init_backfill(object(), target_pool_count=15)
    assert disc.locked_during is True  # lock held while discovering
    assert ctrl._refresh_lock.locked() is False  # released after


async def test_run_init_backfill_releases_lock_on_cancel() -> None:
    class _SlowDisc:
        async def discover(self, *_a: Any, **_k: Any) -> list[str]:
            await asyncio.sleep(60)
            return []

    ctrl = _ctrl(_FakeDB([0]), _SlowDisc())
    task = asyncio.create_task(ctrl.run_init_backfill(object(), target_pool_count=15))
    await asyncio.sleep(0.05)  # let it acquire the lock + enter discover
    assert ctrl._refresh_lock.locked() is True
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ctrl._refresh_lock.locked() is False


async def test_run_init_backfill_drains_copy_before_reporting_success() -> None:
    class _CanonicalDB:
        available = 0

        def count_pool_candidates(self, **_kw: Any) -> int:
            return self.available

    class _Copy:
        def __init__(self, db: _CanonicalDB) -> None:
            self.db = db
            self.calls: list[tuple[Any, int]] = []

        async def drain_pending_expression_copy(self, *, profile: Any, limit: int) -> int:
            self.calls.append((profile, limit))
            self.db.available = 1
            return 1

    db = _CanonicalDB()
    disc = _FakeDisc()
    copy = _Copy(db)
    ctrl = _ctrl(db, disc)
    ctrl.recommendation_engine = copy
    progress: list[tuple[int, int, str]] = []

    async def _progress(done: int, total: int, note: str) -> None:
        progress.append((done, total, note))

    profile = object()
    discovered = await ctrl.run_init_backfill(
        profile,
        target_pool_count=15,
        progress_callback=_progress,
    )

    assert discovered == 2
    assert copy.calls == [(profile, 15)]
    assert db.available == 1
    assert any(done == 2 and "生成首轮推荐文案" in note for done, _total, note in progress)
    assert progress[-1] == (4, 4, "首轮内容池已就绪（1 条可直接浏览）")


async def test_run_init_backfill_rejects_raw_only_result_and_releases_lock() -> None:
    class _RawOnlyDB:
        def count_pool_candidates(self, **_kw: Any) -> int:
            return 0

        def count_pool_readiness(self, **_kw: Any) -> dict[str, int]:
            return {"available": 0, "pending": 2}

    class _NoCopy:
        async def drain_pending_expression_copy(self, *, profile: Any, limit: int) -> int:
            return 0

    ctrl = _ctrl(_RawOnlyDB(), _FakeDisc())
    ctrl.recommendation_engine = _NoCopy()

    with pytest.raises(InitialPoolUnavailableError) as excinfo:
        await ctrl.run_init_backfill(object(), target_pool_count=15)

    assert excinfo.value.discovered_count == 2
    assert excinfo.value.pending_count == 2
    assert ctrl._refresh_lock.locked() is False


def test_llm_work_gate_blocks_while_init_active() -> None:
    """gui-init D1: the controller's background loops pause while a guided init
    is active (account_sync already gates on the same predicate)."""
    ctrl = _ctrl(_FakeDB([0]), _FakeDisc())
    baseline = ctrl._llm_work_allowed()  # no init check wired → underlying gate

    ctrl.init_active_check = lambda: True
    assert ctrl._llm_work_allowed() is False  # forced off regardless of baseline

    ctrl.init_active_check = lambda: False
    assert ctrl._llm_work_allowed() == baseline  # back to the underlying gate

    def _boom() -> bool:
        raise RuntimeError("boom")

    ctrl.init_active_check = _boom  # defensive: a raising check never crashes
    assert ctrl._llm_work_allowed() == baseline
