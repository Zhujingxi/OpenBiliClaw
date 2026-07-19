"""Coordinator for guided (GUI) initialization.

Owns the init lifecycle on a *live* backend (gui-init spec §5):

* single-flight start (TOCTOU) via the ``init_runs`` reservation,
* the **single writer** to the status store + progress events,
* the per-run ``enqueued_task_ids`` set that writer-gating consults to let
  init's own bootstrap task-results through,
* cooperative cancel of the background task.

It holds the :class:`RuntimeContext` (not direct component references) and
reads ``ctx.database`` / ``ctx.event_hub`` / ``ctx.runtime_controller`` lazily
so it always uses the current instances after a config-driven rebuild swaps
them (review R2 A-1).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

_TOTAL_STAGES = 4
_STAGE_LABELS = {
    1: "拉取数据",
    2: "分析偏好",
    3: "生成并保存完整画像",
    4: "生成首轮可用推荐",
}
_ACTIVE = ("starting", "running")
_ORPHAN_HEARTBEAT_SECONDS = 120.0
logger = logging.getLogger(__name__)


# No per-stage duration is published any more. A predicted duration we cannot
# honour is worse than none: the real cost of a stage depends on the selected
# platforms, the collected history AND the provider's latency, so every forecast
# was wrong for someone and a healthy long run read as broken (field report
# 2026-07-20 — the GUI announced "本阶段通常约 3 分钟" / "约 5 分钟" while the
# run legitimately took 30-45+ minutes). The GUI now renders observed facts
# only: elapsed time in the stage plus real ``progress`` counts, and an
# indeterminate bar where no real count exists. Do NOT reintroduce an
# ``eta_seconds`` field here — the CLI keeps its own console-only estimate in
# ``cli._run_with_progress``, which is a separate surface that prints elapsed
# alongside it and explicitly flips to "已超预估、仍在处理".
def _initial_stages() -> list[dict[str, Any]]:
    return [
        {
            "n": n,
            "label": _STAGE_LABELS[n],
            "status": "pending",
            "reason": None,
        }
        for n in range(1, _TOTAL_STAGES + 1)
    ]


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


class InitCoordinator:
    """Lifecycle owner for one guided-init run at a time."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._current_task: asyncio.Task[Any] | None = None
        self._current_run_id: str | None = None
        self._reconcile_tasks: set[asyncio.Task[None]] = set()
        self._enqueued_task_ids: set[str] = set()
        # Serializes status writes + event publishes so liveness heartbeats and
        # substantive stage progress cannot interleave / reorder ``sequence``.
        self._write_lock = asyncio.Lock()
        self._seq = 0

    # ── lazy component access (survives rebuild) ───────────────────────────
    @property
    def _db(self) -> Any:
        return self._ctx.database

    @property
    def _event_hub(self) -> Any:
        return getattr(self._ctx, "event_hub", None)

    # ── boot / liveness ────────────────────────────────────────────────────
    def reconcile_on_boot(self) -> int:
        """Fail stale starting/running runs left by a crash. Returns count."""
        db = self._db
        if db is None:
            return 0
        return int(db.reconcile_init_runs_on_boot())

    def init_active(self) -> bool:
        run = self._db.get_latest_init_run()
        return bool(run and run["status"] in _ACTIVE)

    # ── start / reset (TOCTOU lives in the DB CAS; E2 does cheap pre-checks) ─
    def try_start(self, run_id: str) -> bool:
        """Reserve a new run (single-flight). Seeds the stage list on success."""
        if not self._db.try_reserve_init_starting(run_id):
            return False
        self._enqueued_task_ids = set()
        self._current_task = None
        self._current_run_id = run_id
        self._seq = 0
        self._db.update_init_run(
            run_id, stages_json=json.dumps(_initial_stages(), ensure_ascii=False)
        )
        return True

    def reset_to_idle(self, run_id: str, *, reason: str | None = None) -> None:
        """Roll a reserved-but-not-launched run back (E2 pre-flight reject)."""
        self._db.update_init_run(run_id, status="idle", error_reason=reason)
        if self._current_run_id == run_id and (
            self._current_task is None or self._current_task.done()
        ):
            self._current_task = None
            self._current_run_id = None
            self._enqueued_task_ids.clear()

    # ── bootstrap task ownership (consulted by writer-gating, D1) ──────────
    def register_enqueued_task(self, run_id: str, task_id: str) -> None:
        if self._current_run_id == run_id and self.init_active():
            self._enqueued_task_ids.add(str(task_id))

    def is_owned_bootstrap_task(self, task_id: str) -> bool:
        return self.init_active() and str(task_id) in self._enqueued_task_ids

    def owned_task_ids(self) -> set[str]:
        """Bootstrap task ids enqueued by the active run (empty if idle).

        ``next-task`` consults this so the extension is only handed init's own
        bootstrap work while a run is active — never a stale pending task that
        would otherwise starve the run's collectors (gui-init review)."""
        if not self.init_active():
            return set()
        return set(self._enqueued_task_ids)

    # ── background task handle (for cancel) ────────────────────────────────
    def attach_task(self, run_id: str, task: asyncio.Task[Any]) -> None:
        self._current_run_id = run_id
        self._current_task = task

        def _on_done(_task: asyncio.Task[Any]) -> None:
            # The normal wrapper persists completed/failed/cancelled before it
            # exits. If that terminal write itself failed, the DB would still
            # say running while the only owning task is already gone. Recheck
            # after every task exit and close that lease deterministically.
            async def _recover() -> None:
                try:
                    await self.reconcile_orphaned_run(run_id, force_task_done=True)
                except Exception:
                    logger.exception("failed to reconcile finished init task %s", run_id)
                finally:
                    if self._current_run_id == run_id and self._current_task is _task:
                        self._current_task = None
                        self._current_run_id = None
                        self._enqueued_task_ids.clear()

            recovery = asyncio.create_task(_recover())
            self._reconcile_tasks.add(recovery)
            recovery.add_done_callback(self._reconcile_tasks.discard)

        task.add_done_callback(_on_done)

    async def cancel_current_run(self, run_id: str) -> bool:
        """Request cancellation of the running task. The wrapper's ``finally``
        persists the ``cancelled`` status (single-writer; spec §5f)."""
        task = self._current_task
        if self._current_run_id != run_id or task is None or task.done():
            return False
        task.cancel()
        return True

    async def reconcile_orphaned_run(
        self,
        run_id: str | None = None,
        *,
        max_heartbeat_age_seconds: float = _ORPHAN_HEARTBEAT_SECONDS,
        force_task_done: bool = False,
    ) -> bool:
        """Fail an active row whose owning task has disappeared.

        A newly-reserved ``starting`` row intentionally has a short window
        before :meth:`attach_task`, so a missing handle is only considered an
        orphan once its heartbeat lease expires. A known completed task is
        definitive and is reconciled immediately. Returns whether a row was
        changed.
        """
        async with self._write_lock:
            run = self._db.get_latest_init_run()
            if run is None or run.get("status") not in _ACTIVE:
                return False
            active_run_id = str(run.get("run_id") or "")
            if run_id is not None and active_run_id != str(run_id):
                return False

            owns_task = self._current_run_id == active_run_id
            task = self._current_task if owns_task else None
            task_done = bool(task is not None and task.done())
            heartbeat_age = _timestamp_age_seconds(run.get("updated_at"))
            lease_expired_without_owner = task is None and heartbeat_age >= max(
                0.0, float(max_heartbeat_age_seconds)
            )
            if not task_done and not lease_expired_without_owner:
                return False
            if force_task_done and task is not None and not task_done:
                return False

            stages = json.loads(run["stages_json"]) if run.get("stages_json") else _initial_stages()
            for stage in stages:
                if stage.get("status") in ("running", "pending"):
                    stage["status"] = "failed"
                    stage["reason"] = "interrupted"
                    stage.pop("progress", None)
            self._seq = max(self._seq, int(run.get("sequence") or 0)) + 1
            now = _utcnow_iso()
            self._db.update_init_run(
                active_run_id,
                status="failed",
                stages_json=json.dumps(stages, ensure_ascii=False),
                error_reason="interrupted",
                error_detail="初始化后台任务已结束，但未能写入终态；已自动释放运行锁。",
                sequence=self._seq,
                progress_sequence=self._seq,
                progress_at=now,
                finished_at=now,
            )
            if self._event_hub is not None:
                with contextlib.suppress(Exception):
                    await self._event_hub.publish(
                        {
                            "type": "init_failed",
                            "run_id": active_run_id,
                            "sequence": self._seq,
                            "stage": _current_stage(stages),
                            "total": _TOTAL_STAGES,
                            "reason": "interrupted",
                        }
                    )
            if owns_task:
                self._current_task = None
                self._current_run_id = None
                self._enqueued_task_ids.clear()
            return True

    # ── single status writer ───────────────────────────────────────────────
    async def _write(
        self,
        run_id: str,
        *,
        status: str | None = None,
        stage: int | None = None,
        stage_status: str | None = None,
        stage_reason: str | None = None,
        partial_success: bool | None = None,
        error_reason: str | None = None,
        error_detail: str | None = None,
        finished: bool = False,
        stage_progress: dict[str, Any] | None = None,
        event_type: str | None = None,
        event_extra: dict[str, Any] | None = None,
        substantive: bool = True,
    ) -> int:
        async with self._write_lock:
            run = self._db.get_latest_init_run()
            # A terminal run can be followed by a new reservation before every
            # old callback/heartbeat has unwound. Never let a late writer build
            # its stage payload from the new run's row or publish a stale event.
            if run is None or str(run.get("run_id") or "") != str(run_id):
                return int(run.get("sequence") or self._seq) if run is not None else self._seq
            # The first terminal write owns the final snapshot. A provider task
            # can finish just after its sibling failed (or can briefly ignore
            # cancellation); its late heartbeat/progress must not mutate the
            # diagnostic timeline or replace failure with completion.
            if str(run.get("status") or "") not in _ACTIVE:
                return int(run.get("sequence") or self._seq)
            stages = json.loads(run["stages_json"]) if run.get("stages_json") else _initial_stages()
            # Defense in depth for the central initialization invariant: stage
            # 4 may never observe a draft/missing profile, even if a future
            # caller accidentally regresses the pipeline ordering.
            if stage == 4 and stage_status == "running":
                profile_stage = next((s for s in stages if int(s.get("n", 0)) == 3), None)
                if profile_stage is None or profile_stage.get("status") not in ("ok", "warning"):
                    raise RuntimeError(
                        "cannot start init stage 4 before the full profile stage is committed"
                    )
            if stage is not None and stage_status is not None:
                for s in stages:
                    if s["n"] == stage:
                        s["status"] = stage_status
                        s["reason"] = stage_reason
                        # A stage leaving "running" (ok / warning / failed) has
                        # no live sub-progress anymore — drop it so the GUI
                        # doesn't render a stale "第 3/8 批" on a done stage.
                        if stage_status != "running":
                            s.pop("progress", None)
            # A pure sub-progress write attaches the {done,total,note} payload
            # to the (still-running) stage without touching its status.
            if stage is not None and stage_progress is not None:
                for s in stages:
                    if s["n"] == stage:
                        s["progress"] = stage_progress
            # On a terminal failure/cancel, downgrade any still-"running" or
            # "pending" stage so status consumers (and the extension checklist,
            # which keys off stage status) don't show a non-terminal timeline
            # for a finished run (gui-init review).
            if status in ("failed", "cancelled"):
                for s in stages:
                    if s["status"] in ("running", "pending"):
                        s["status"] = status
                        s.pop("progress", None)
                        if s.get("reason") is None:
                            s["reason"] = error_reason
            self._seq = max(self._seq, int(run.get("sequence") or 0) if run else 0) + 1
            fields: dict[str, Any] = {
                "sequence": self._seq,
                "stages_json": json.dumps(stages, ensure_ascii=False),
            }
            if substantive:
                fields["progress_sequence"] = self._seq
                fields["progress_at"] = _utcnow_iso()
            if status is not None:
                fields["status"] = status
            if stage is not None:
                fields["stage"] = stage
            if partial_success is not None:
                fields["partial_success"] = 1 if partial_success else 0
            if error_reason is not None:
                fields["error_reason"] = error_reason
            if error_detail is not None:
                fields["error_detail"] = error_detail
            if finished:
                fields["finished_at"] = _utcnow_iso()
            self._db.update_init_run(run_id, **fields)

            if event_type and self._event_hub is not None:
                event: dict[str, Any] = {
                    "type": event_type,
                    "run_id": run_id,
                    "sequence": self._seq,
                    "stage": stage if stage is not None else _current_stage(stages),
                    "total": _TOTAL_STAGES,
                }
                if event_extra:
                    event.update(event_extra)
                with contextlib.suppress(Exception):
                    await self._event_hub.publish(event)
            return self._seq

    async def mark_running(self, run_id: str) -> None:
        await self._write(run_id, status="running")

    async def stage_started(self, run_id: str, stage: int) -> None:
        await self._write(
            run_id,
            status="running",
            stage=stage,
            stage_status="running",
            event_type="init_progress",
        )

    async def stage_done(
        self, run_id: str, stage: int, *, status: str = "ok", reason: str | None = None
    ) -> None:
        await self._write(
            run_id,
            stage=stage,
            stage_status=status,
            stage_reason=reason,
            event_type="init_progress",
        )

    async def stage_progress(
        self,
        run_id: str,
        stage: int,
        *,
        done: int = 0,
        total: int = 0,
        note: str | None = None,
        mode: str = "determinate",
        elapsed_seconds: int | None = None,
        max_seconds: int | None = None,
        substantive: bool = True,
    ) -> None:
        """Report fine-grained progress within a running stage (spec Phase 0).

        ``done`` is clamped to ``0 ≤ done ≤ total``; a non-positive ``total`` is
        ignored (no write, no event) so a producer that hasn't sized its work
        yet can't stamp a meaningless 0/0. Publishes ``init_progress`` carrying
        the payload so SSE-driven pages advance without waiting for the poll.
        """
        normalized_mode = "indeterminate" if mode == "indeterminate" else "determinate"
        if normalized_mode == "determinate" and total <= 0:
            return
        safe_total = max(0, int(total))
        clamped = max(0, min(int(done), safe_total)) if safe_total else 0
        payload: dict[str, Any] = {
            "done": clamped,
            "total": safe_total,
            "note": note,
            "mode": normalized_mode,
        }
        if elapsed_seconds is not None:
            payload["elapsed_seconds"] = max(0, int(elapsed_seconds))
        if max_seconds is not None:
            payload["max_seconds"] = max(0, int(max_seconds))
        await self._write(
            run_id,
            stage=stage,
            stage_progress=payload,
            event_type="init_progress",
            event_extra={"progress": payload},
            substantive=substantive,
        )

    async def touch(self, run_id: str) -> None:
        """Liveness heartbeat: bump ``sequence`` + ``updated_at`` only.

        It does not advance ``progress_sequence`` / ``progress_at`` and does not
        publish an event. Front ends can therefore distinguish "backend owner is
        alive" from "the current operation made useful progress" without
        flooding SSE with content-free frames.
        """
        await self._write(run_id, substantive=False)

    async def complete(
        self,
        run_id: str,
        *,
        partial_success: bool = False,
        reason: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Finish a run, retaining a diagnosable partial-success reason.

        A discovery timeout is deliberately not a hard failure once the profile
        exists. ``reason`` / ``detail`` let status consumers explain that
        degraded terminal state instead of presenting it as a silent success.
        """
        await self._write(
            run_id,
            status="completed",
            partial_success=partial_success,
            error_reason=reason,
            error_detail=detail,
            finished=True,
            event_type="init_completed",
            event_extra={
                "partial_success": partial_success,
                "reason": reason or "none",
                "detail": detail or "",
            },
        )

    async def fail(self, run_id: str, reason: str, detail: str | None = None) -> None:
        """Terminal failure. ``detail`` carries the human-readable specifics
        (GuidedInitError message / exception summary) so status consumers can
        show WHY instead of only the generic reason code."""
        await self._write(
            run_id,
            status="failed",
            error_reason=reason,
            error_detail=(detail or "").strip() or None,
            finished=True,
            event_type="init_failed",
            event_extra={"reason": reason},
        )

    async def cancel(self, run_id: str, reason: str = "cancelled") -> None:
        await self._write(
            run_id,
            status="cancelled",
            error_reason=reason,
            finished=True,
            event_type="init_failed",
            event_extra={"reason": reason},
        )

    # ── status read (run-derived part; E1 adds prereqs/can_manage) ─────────
    def get_status(self) -> dict[str, Any]:
        run = self._db.get_latest_init_run()
        if run is None:
            return {
                "running": False,
                "run_id": None,
                "sequence": 0,
                "current_stage": 0,
                "total_stages": _TOTAL_STAGES,
                "stages": _initial_stages(),
                "partial_success": False,
                "status": "idle",
                "reason": "none",
                "detail": "",
                "last_activity": "",
                "last_heartbeat_at": "",
                "last_progress_at": "",
                "progress_sequence": 0,
            }
        stages = json.loads(run["stages_json"]) if run.get("stages_json") else _initial_stages()
        return {
            "running": run["status"] in _ACTIVE,
            "run_id": run["run_id"],
            "sequence": run["sequence"],
            "current_stage": _current_stage(stages),
            "total_stages": _TOTAL_STAGES,
            "stages": stages,
            "partial_success": bool(run["partial_success"]),
            "status": run["status"],
            "reason": run["error_reason"] or "none",
            "detail": str(run.get("error_detail") or ""),
            # ``last_activity`` stays as a compatibility alias for old clients.
            # New clients separately render backend liveness and useful progress.
            "last_activity": str(run.get("updated_at") or ""),
            "last_heartbeat_at": str(run.get("updated_at") or ""),
            "last_progress_at": str(run.get("progress_at") or run.get("updated_at") or ""),
            "progress_sequence": int(run.get("progress_sequence") or 0),
        }


def _timestamp_age_seconds(value: Any) -> float:
    """Best-effort age for SQLite/ISO timestamps; malformed means expired."""
    raw = str(value or "").strip()
    if not raw:
        return float("inf")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())
    except (TypeError, ValueError):
        return float("inf")


def _current_stage(stages: Sequence[dict[str, Any]]) -> int:
    """Lowest still-running stage; else the highest completed; else 0 (spec §5e)."""
    running = [int(s["n"]) for s in stages if s["status"] == "running"]
    if running:
        return min(running)
    # A terminal write marks the failed/cancelled stage and every later
    # pending stage terminal so clients never render a live-looking timeline.
    # The first such stage is the actual stop point; taking the highest would
    # misleadingly report stage 4 when stage 2 was cancelled.
    terminal = [int(s["n"]) for s in stages if s["status"] in ("failed", "cancelled")]
    if terminal:
        return min(terminal)
    done = [int(s["n"]) for s in stages if s["status"] in ("ok", "warning", "failed")]
    return max(done) if done else 0
