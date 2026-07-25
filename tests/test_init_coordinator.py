"""Tests for InitCoordinator (gui-init spec §5, plan A2)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.runtime.init_coordinator import InitCoordinator
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class _FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> bool:
        self.events.append(event)
        return True


def _coord(tmp_path: Path) -> tuple[InitCoordinator, Database, _FakeHub]:
    db = Database(tmp_path / "init.db")
    db.initialize()
    hub = _FakeHub()
    ctx = SimpleNamespace(database=db, event_hub=hub, runtime_controller=None)
    return InitCoordinator(ctx), db, hub


def test_try_start_single_flight_and_seeds_stages(tmp_path: Path) -> None:
    coord, db, _ = _coord(tmp_path)
    assert coord.try_start("run-1") is True
    assert coord.init_active() is True
    run = db.get_latest_init_run()
    stages = json.loads(run["stages_json"])
    assert [s["n"] for s in stages] == [1, 2, 3, 4]
    assert all(s["status"] == "pending" for s in stages)
    # Second start blocked while active.
    assert coord.try_start("run-2") is False


def test_reconcile_on_boot_delegates(tmp_path: Path) -> None:
    coord, db, _ = _coord(tmp_path)
    coord.try_start("run-1")
    db.update_init_run("run-1", status="running")
    assert coord.reconcile_on_boot() == 1
    assert coord.init_active() is False


async def test_lifecycle_emits_progress_then_completed(tmp_path: Path) -> None:
    coord, db, hub = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.mark_running("run-1")
    for n in (1, 2, 3, 4):
        await coord.stage_started("run-1", n)
        await coord.stage_done("run-1", n)
    await coord.complete("run-1", partial_success=False)

    run = db.get_latest_init_run()
    assert run["status"] == "completed"
    assert all(s["status"] == "ok" for s in json.loads(run["stages_json"]))
    # sequence strictly increasing across all writes.
    seqs = [e["sequence"] for e in hub.events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert hub.events[-1]["type"] == "init_completed"
    assert any(e["type"] == "init_progress" for e in hub.events)


async def test_partial_completion_persists_reason_and_emits_detail(tmp_path: Path) -> None:
    coord, db, hub = _coord(tmp_path)
    coord.try_start("run-partial")

    await coord.complete(
        "run-partial",
        partial_success=True,
        reason="discovery_timeout",
        detail="首轮内容池等待超过上限，系统会在后台继续补齐。",
    )

    run = db.get_latest_init_run()
    assert run["status"] == "completed"
    assert run["partial_success"] == 1
    assert run["error_reason"] == "discovery_timeout"
    assert "后台继续补齐" in run["error_detail"]
    assert hub.events[-1]["partial_success"] is True
    assert hub.events[-1]["reason"] == "discovery_timeout"
    assert "后台继续补齐" in hub.events[-1]["detail"]


async def test_fail_marks_failed_with_reason(tmp_path: Path) -> None:
    coord, db, hub = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.fail("run-1", "llm_not_ready")
    run = db.get_latest_init_run()
    assert run["status"] == "failed"
    assert run["error_reason"] == "llm_not_ready"
    assert hub.events[-1] == {
        "type": "init_failed",
        "run_id": "run-1",
        "sequence": hub.events[-1]["sequence"],
        "stage": hub.events[-1]["stage"],
        "total": 4,
        "reason": "llm_not_ready",
    }


async def test_fail_stores_detail_and_status_exposes_it(tmp_path: Path) -> None:
    """``fail(detail=...)`` persists the failure specifics and get_status
    surfaces them, so an internal_error is diagnosable from the UI without
    server logs (field report 2026-07-05)."""
    coord, db, _ = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.fail("run-1", "internal_error", detail="RuntimeError: boom during stage 2")
    run = db.get_latest_init_run()
    assert run["error_detail"] == "RuntimeError: boom during stage 2"
    status = coord.get_status()
    assert status["reason"] == "internal_error"
    assert status["detail"] == "RuntimeError: boom during stage 2"

    # Re-reserving the SAME run_id (ON CONFLICT path) clears the stale detail.
    assert coord.try_start("run-1") is True
    assert db.get_latest_init_run()["error_detail"] is None
    assert coord.get_status()["detail"] == ""


async def test_fail_without_detail_keeps_empty_detail(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.fail("run-1", "empty_history")
    assert coord.get_status()["detail"] == ""


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
async def test_terminal_current_stage_is_the_actual_stop_point(
    tmp_path: Path, terminal: str
) -> None:
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-terminal")
    await coord.mark_running("run-terminal")
    await coord.stage_started("run-terminal", 1)
    await coord.stage_done("run-terminal", 1)
    await coord.stage_started("run-terminal", 2)

    if terminal == "failed":
        await coord.fail("run-terminal", "analyze_failed")
    else:
        await coord.cancel("run-terminal")

    status = coord.get_status()
    assert status["current_stage"] == 2
    assert [stage["status"] for stage in status["stages"]] == [
        "ok",
        terminal,
        terminal,
        terminal,
    ]


async def test_stage_4_cannot_start_before_full_profile_is_committed(tmp_path: Path) -> None:
    coord, db, _ = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.mark_running("run-1")
    await coord.stage_started("run-1", 3)
    with pytest.raises(RuntimeError, match="full profile stage"):
        await coord.stage_started("run-1", 4)

    # The rejected transition is side-effect free. Once stage 3's committed
    # barrier is recorded, stage 4 may start normally.
    assert db.get_latest_init_run()["sequence"] == 2
    await coord.stage_done("run-1", 3)
    await coord.stage_started("run-1", 4)
    status = coord.get_status()
    by_n = {s["n"]: s["status"] for s in status["stages"]}
    assert by_n[3] == "ok" and by_n[4] == "running"
    assert status["current_stage"] == 4


def test_bootstrap_task_ownership(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-1")
    coord.register_enqueued_task("stale-run", "task-stale")
    coord.register_enqueued_task("run-1", "task-abc")
    assert coord.is_owned_bootstrap_task("task-abc") is True
    assert coord.is_owned_bootstrap_task("task-stale") is False
    assert coord.is_owned_bootstrap_task("task-other") is False


def test_unowned_when_not_active(tmp_path: Path) -> None:
    coord, db, _ = _coord(tmp_path)
    coord.try_start("run-1")
    coord.register_enqueued_task("run-1", "task-abc")
    db.update_init_run("run-1", status="completed")
    assert coord.is_owned_bootstrap_task("task-abc") is False


async def test_cancel_current_run_cancels_task(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-1")

    async def _long() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(_long())
    coord.attach_task("run-1", task)
    assert await coord.cancel_current_run("run-1") is True
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_cancel_current_run_rejects_stale_run_id(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-current")

    async def _long() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(_long())
    coord.attach_task("run-current", task)
    try:
        assert await coord.cancel_current_run("run-stale") is False
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_late_status_write_cannot_touch_newer_run(tmp_path: Path) -> None:
    coord, db, hub = _coord(tmp_path)
    coord.try_start("run-old")
    await coord.fail("run-old", "cancelled")
    assert coord.try_start("run-new") is True
    new_before = db.get_latest_init_run()
    events_before = list(hub.events)

    await coord.touch("run-old")
    await coord.stage_started("run-old", 1)

    new_after = db.get_latest_init_run()
    assert new_after["run_id"] == "run-new"
    assert new_after["sequence"] == new_before["sequence"]
    assert new_after["stages_json"] == new_before["stages_json"]
    assert hub.events == events_before


async def test_late_status_writes_cannot_mutate_same_terminal_run(tmp_path: Path) -> None:
    coord, db, hub = _coord(tmp_path)
    coord.try_start("run-terminal")
    await coord.mark_running("run-terminal")
    await coord.stage_started("run-terminal", 2)
    await coord.stage_progress("run-terminal", 2, done=4, total=6, note="第 4/6 批")
    await coord.fail("run-terminal", "analyze_failed", detail="chunk 6 failed")
    terminal_before = dict(db.get_latest_init_run())
    events_before = list(hub.events)

    await coord.touch("run-terminal")
    await coord.stage_progress("run-terminal", 2, done=5, total=6, note="迟到 sibling")
    await coord.stage_done("run-terminal", 2)
    await coord.complete("run-terminal")

    assert db.get_latest_init_run() == terminal_before
    assert hub.events == events_before


def test_get_status_idle_when_empty(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    status = coord.get_status()
    assert status["running"] is False
    assert status["status"] == "idle"
    assert status["current_stage"] == 0
    # last_activity is "" when there is no run row (backward-compatible add).
    assert status["last_activity"] == ""


# ── init-progress-visibility Phase 0: sub-progress / heartbeat / eta ─────────


def test_initial_stages_publish_no_duration_forecast(tmp_path: Path) -> None:
    """Stages carry no ``eta_seconds``.

    A predicted duration we cannot honour is worse than none: the real cost
    depends on the selected platforms, the collected history and the provider's
    latency, so every forecast was wrong for someone and made a healthy long
    run read as broken (field report 2026-07-20). The GUI renders observed
    elapsed time + real progress counts instead.
    """
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-1")
    stages = coord.get_status()["stages"]
    assert [s["n"] for s in stages] == [1, 2, 3, 4]
    assert all("eta_seconds" not in s for s in stages)
    assert set(stages[0]) == {"n", "label", "status", "reason"}


def test_coordinator_module_exposes_no_stage_duration_constants() -> None:
    """Guard against reintroducing a forecast the GUI would render."""
    from openbiliclaw.runtime import init_coordinator

    assert not hasattr(init_coordinator, "_STAGE_ETAS")
    assert not hasattr(init_coordinator, "derive_stage_etas")
    assert not hasattr(InitCoordinator, "set_stage_eta")


async def test_stage_progress_persists_and_emits(tmp_path: Path) -> None:
    coord, _, hub = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.mark_running("run-1")
    await coord.stage_started("run-1", 2)
    await coord.stage_progress("run-1", 2, done=1, total=8, note="第 1/8 批")

    stages = {s["n"]: s for s in coord.get_status()["stages"]}
    expected = {"done": 1, "total": 8, "note": "第 1/8 批", "mode": "determinate"}
    assert stages[2]["progress"] == expected
    # An init_progress event was published carrying the progress payload.
    prog_events = [e for e in hub.events if e.get("type") == "init_progress"]
    assert prog_events[-1]["progress"] == expected


async def test_stage_progress_clamps_done_into_range(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.stage_started("run-1", 2)
    await coord.stage_progress("run-1", 2, done=99, total=8)
    stages = {s["n"]: s for s in coord.get_status()["stages"]}
    assert stages[2]["progress"]["done"] == 8
    await coord.stage_progress("run-1", 2, done=-5, total=8)
    stages = {s["n"]: s for s in coord.get_status()["stages"]}
    assert stages[2]["progress"]["done"] == 0


async def test_stage_progress_ignores_nonpositive_total(tmp_path: Path) -> None:
    coord, db, hub = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.stage_started("run-1", 2)
    seq_before = db.get_latest_init_run()["sequence"]
    events_before = len(hub.events)
    await coord.stage_progress("run-1", 2, done=1, total=0)
    # total <= 0 is a no-op: no write, no sequence bump, no event.
    assert db.get_latest_init_run()["sequence"] == seq_before
    assert len(hub.events) == events_before
    stages = {s["n"]: s for s in coord.get_status()["stages"]}
    assert stages[2].get("progress") in (None, {})


async def test_indeterminate_stage_progress_accepts_zero_total(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.stage_started("run-1", 3)
    await coord.stage_progress(
        "run-1",
        3,
        mode="indeterminate",
        elapsed_seconds=40,
        max_seconds=360,
        note="AI 正在综合画像",
        substantive=False,
    )
    progress = {s["n"]: s for s in coord.get_status()["stages"]}[3]["progress"]
    assert progress == {
        "done": 0,
        "total": 0,
        "note": "AI 正在综合画像",
        "mode": "indeterminate",
        "elapsed_seconds": 40,
        "max_seconds": 360,
    }


async def test_stage_done_clears_progress(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.stage_started("run-1", 2)
    await coord.stage_progress("run-1", 2, done=3, total=8)
    await coord.stage_done("run-1", 2)
    stages = {s["n"]: s for s in coord.get_status()["stages"]}
    assert stages[2]["status"] == "ok"
    assert stages[2].get("progress") is None


async def test_touch_bumps_sequence_without_publishing_event(tmp_path: Path) -> None:
    coord, db, hub = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.mark_running("run-1")
    row_before = db.get_latest_init_run()
    seq_before = row_before["sequence"]
    progress_seq_before = row_before["progress_sequence"]
    progress_at_before = row_before["progress_at"]
    events_before = len(hub.events)
    await coord.touch("run-1")
    assert db.get_latest_init_run()["sequence"] == seq_before + 1
    row_after = db.get_latest_init_run()
    assert row_after["progress_sequence"] == progress_seq_before
    assert row_after["progress_at"] == progress_at_before
    # Invariant 5: touch must NOT publish an init_progress (or any) event.
    assert len(hub.events) == events_before


async def test_get_status_exposes_last_activity(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.mark_running("run-1")
    status = coord.get_status()
    assert status["last_activity"] != ""
    assert status["last_heartbeat_at"] == status["last_activity"]
    assert status["last_progress_at"] != ""
    assert status["progress_sequence"] > 0


async def test_finished_owner_without_terminal_write_is_reconciled(tmp_path: Path) -> None:
    coord, db, hub = _coord(tmp_path)
    coord.try_start("run-orphan")
    await coord.mark_running("run-orphan")

    async def _returns_without_terminal_write() -> None:
        return None

    task = asyncio.create_task(_returns_without_terminal_write())
    coord.attach_task("run-orphan", task)
    await task
    # Done callback schedules the lease audit on the same loop.
    for _ in range(5):
        await asyncio.sleep(0)
        if db.get_latest_init_run()["status"] == "failed":
            break

    run = db.get_latest_init_run()
    assert run["status"] == "failed"
    assert run["error_reason"] == "interrupted"
    assert "自动释放运行锁" in run["error_detail"]
    assert coord.init_active() is False
    assert hub.events[-1]["type"] == "init_failed"


async def test_expired_starting_lease_without_task_is_reconciled(tmp_path: Path) -> None:
    coord, db, _ = _coord(tmp_path)
    coord.try_start("run-abandoned")
    db.conn.execute(
        "UPDATE init_runs SET updated_at = '2000-01-01 00:00:00' WHERE run_id = ?",
        ("run-abandoned",),
    )
    db.conn.commit()

    assert await coord.reconcile_orphaned_run(max_heartbeat_age_seconds=1) is True
    run = db.get_latest_init_run()
    assert run["status"] == "failed"
    assert run["error_reason"] == "interrupted"
    assert coord.try_start("run-after-abandoned") is True


async def test_interleaved_stage_progress_and_touch_sequence_monotonic(tmp_path: Path) -> None:
    coord, db, _ = _coord(tmp_path)
    coord.try_start("run-1")  # sequence starts at 0
    await coord.stage_started("run-1", 2)  # sequence -> 1

    async def _op(i: int) -> None:
        if i % 2 == 0:
            await coord.touch("run-1")
        else:
            await coord.stage_progress("run-1", 2, done=i, total=100)

    await asyncio.gather(*(_op(i) for i in range(100)))
    # stage_started (1) + 100 writes == exactly 101, strictly serialized.
    assert db.get_latest_init_run()["sequence"] == 101


# ── A3: RuntimeContext wiring ──────────────────────────────────────────────


def test_runtime_context_exposes_lazy_init_coordinator(tmp_path: Path) -> None:
    from openbiliclaw.api.runtime_context import RuntimeContext

    db = Database(tmp_path / "ctx.db")
    db.initialize()
    ctx = RuntimeContext(database=db)
    c1 = ctx.init_coordinator
    assert isinstance(c1, InitCoordinator)
    assert ctx.init_coordinator is c1  # memoized singleton
    # Reads ctx.database lazily, so it actually drives the wired DB.
    assert c1.try_start("r1") is True
    assert db.get_latest_init_run()["run_id"] == "r1"


def test_init_prereqs_enabled_platforms_include_reddit() -> None:
    from types import SimpleNamespace

    from openbiliclaw.runtime.init_prereqs import InitPrereqs

    ctx = SimpleNamespace(
        config=SimpleNamespace(
            sources=SimpleNamespace(
                bilibili=SimpleNamespace(enabled=True),
                xiaohongshu=SimpleNamespace(enabled=False),
                douyin=SimpleNamespace(enabled=False),
                youtube=SimpleNamespace(enabled=False),
                twitter=SimpleNamespace(enabled=False),
                zhihu=SimpleNamespace(enabled=False),
                reddit=SimpleNamespace(enabled=True),
            )
        )
    )

    assert InitPrereqs(ctx).enabled_platforms() == ["bilibili", "reddit"]


def test_coordinator_reads_ctx_components_lazily_not_cached(tmp_path: Path) -> None:
    from openbiliclaw.api.runtime_context import RuntimeContext

    db1 = Database(tmp_path / "a.db")
    db1.initialize()
    ctx = RuntimeContext(database=db1)
    coord = ctx.init_coordinator
    # Swap a component on the ctx (mirrors hot-reload swapping runtime_controller):
    # the same coordinator must use the new instance, not one cached at build.
    db2 = Database(tmp_path / "b.db")
    db2.initialize()
    ctx.database = db2
    coord.try_start("r2")
    assert db2.get_latest_init_run()["run_id"] == "r2"
    assert db1.get_latest_init_run() is None


def test_startup_reconciles_stale_init_run(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app

    db = Database(tmp_path / "startup.db")
    db.initialize()
    db.try_reserve_init_starting("stale")
    db.update_init_run("stale", status="running")

    app = create_app(memory_manager=object(), database=db, soul_engine=object())
    with TestClient(app):  # entering triggers the startup event
        pass

    run = db.get_latest_init_run()
    assert run["status"] == "failed"
    assert run["error_reason"] == "interrupted"


def test_init_status_endpoint_shape(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app

    db = Database(tmp_path / "e1.db")
    db.initialize()
    app = create_app(memory_manager=object(), database=db, soul_engine=object())
    with TestClient(app) as client:
        resp = client.get("/api/init-status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["initialized"] is False
    assert body["total_stages"] == 4
    assert len(body["stages"]) == 4
    # No configured cookie / chat creds in this minimal app → can't start.
    assert body["prerequisites"]["bilibili_check"] == "failed"
    assert body["can_start"] is False
    # local_only: TestClient's peer is not loopback, and v0.3.155+ the reason
    # ladder surfaces the untrusted cause instead of falling through to a
    # generic "none".
    assert body["reason"] in (
        "local_only",
        "bilibili_not_logged_in",
        "unsupported_runtime",
        "llm_not_ready",
    )


def test_background_llm_work_paused_during_init(tmp_path: Path) -> None:
    """D1: all daemon-owned background LLM loops pause while init is active."""
    from openbiliclaw.api.runtime_context import RuntimeContext

    db = Database(tmp_path / "d1.db")
    db.initialize()
    ctx = RuntimeContext(database=db)
    # Idle baseline is whatever presence/scheduler gating decides; the contract
    # under test is the init short-circuit forcing False.
    ctx.init_coordinator.try_start("r1")
    assert ctx.background_llm_work_allowed() is False


def test_background_llm_work_paused_before_first_profile(tmp_path: Path) -> None:
    """Daemon account-sync must not pre-empt guided init on a fresh install."""
    from types import SimpleNamespace

    from openbiliclaw.api.runtime_context import RuntimeContext

    db = Database(tmp_path / "pre-profile.db")
    db.initialize()
    ctx = RuntimeContext(
        database=db,
        soul_engine=SimpleNamespace(is_profile_ready=lambda: False),
    )

    assert ctx.init_coordinator.init_active() is False
    assert ctx.background_llm_work_allowed() is False
