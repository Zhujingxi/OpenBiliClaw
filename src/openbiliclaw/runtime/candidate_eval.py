"""Continuous, work-conserving discovery-candidate evaluation."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openbiliclaw.llm.base import classify_llm_failure_kind

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_RATE_LIMIT_BACKOFF_SECONDS = (15.0, 30.0, 60.0, 120.0, 300.0)
_TRANSIENT_BACKOFF_SECONDS = (15.0, 30.0, 60.0, 120.0, 300.0)
_NO_PROGRESS_BACKOFF_SECONDS = (60.0, 120.0, 300.0)


@dataclass(frozen=True)
class CandidateEvalSnapshot:
    """Durable inventory counts used for coordinator decisions."""

    available: int
    target: int
    pending_eval: int
    evaluating: int
    evaluated_pending_admission: int
    admitted_pending_copy: int


def effective_candidate_eval_workers(configured: int, llm_concurrency: int) -> int:
    """Reserve one global LLM slot while allocating candidate workers."""

    desired = max(1, min(8, int(configured)))
    global_limit = max(1, int(llm_concurrency))
    return min(desired, max(1, global_limit - 1))


class CandidateEvalCoordinator:
    """Own claims, parallelize LLM work, and serialize completion writes."""

    def __init__(
        self,
        *,
        pipeline: Any,
        snapshot_provider: Any,
        profile_provider: Any,
        worker_count: int = 3,
        batch_size: int = 30,
        supply_callback: Any | None = None,
        post_commit_callback: Any | None = None,
        on_admitted: Callable[[int], None] | None = None,
        work_allowed: Any | None = None,
        safety_wake_seconds: float = 60.0,
        time_fn: Any = time.monotonic,
    ) -> None:
        self.pipeline = pipeline
        self.snapshot_provider = snapshot_provider
        self.profile_provider = profile_provider
        self.worker_count = max(1, min(8, int(worker_count)))
        self.batch_size = max(1, min(30, int(batch_size)))
        self.supply_callback = supply_callback
        self.post_commit_callback = post_commit_callback
        self.on_admitted = on_admitted
        self.work_allowed = work_allowed
        self.safety_wake_seconds = max(0.01, float(safety_wake_seconds))
        self.time_fn = time_fn

        self._wake_event = asyncio.Event()
        self._generation = 0
        self._workers: dict[asyncio.Task[Any], Any] = {}
        self._supply_task: asyncio.Task[Any] | None = None
        self._post_commit_task: asyncio.Task[Any] | None = None
        self._post_commit_requested = False
        self._cleanup_lock = asyncio.Lock()
        self._released_tokens: set[str] = set()
        self._stopping = False
        self._running = False
        self._paused = False
        self._backoff_until = 0.0
        self._rate_limit_streak = 0
        self._transient_streak = 0
        self._zero_cache_streak = 0
        self._no_progress_level = 0

        self.state = "idle"
        self.last_wake_reason = ""
        self.last_error = ""
        self.last_batch_seconds = 0.0
        self.last_cached = 0
        self.last_rejected = 0

    def notify(self, reason: str) -> None:
        """Publish a level-triggered wake-up without losing boundary races."""

        self._generation += 1
        self.last_wake_reason = str(reason)
        if self._paused and self._resume_notification(reason):
            self._paused = False
            self._backoff_until = 0.0
        self._wake_event.set()

    async def run_forever(self) -> None:
        """Continuously fill open evaluator slots until stopped or at target."""

        if self._running:
            return
        self._running = True
        self.notify("startup")
        try:
            while not self._stopping:
                await self._commit_finished_workers()
                if self._stopping:
                    break

                await self._settle_supply_task()
                await self._settle_post_commit_task()
                if self.work_allowed is not None and not bool(self.work_allowed()):
                    self.state = "paused"
                    await self._wait_for_activity(self.safety_wake_seconds)
                    continue
                now = self.time_fn()
                if self._paused:
                    self.state = "paused"
                    await self._wait_for_activity(self.safety_wake_seconds)
                    continue
                if self._backoff_until > now:
                    self.state = "backoff"
                    await self._wait_for_activity(
                        min(self.safety_wake_seconds, self._backoff_until - now)
                    )
                    continue
                self._backoff_until = 0.0

                snapshot = self._snapshot()
                self._admit_evaluated(snapshot)
                snapshot = self._snapshot()
                if self._projected_inventory(snapshot) >= snapshot.target:
                    self.state = "idle"
                else:
                    self._fill_open_slots()
                    snapshot = self._snapshot()
                    if not self._workers and snapshot.pending_eval <= 0:
                        self._request_supply("candidate_supply")
                        self.state = "waiting_supply" if self._supply_task else "idle"
                    elif self._workers:
                        self.state = "running"

                await self._wait_for_activity(self.safety_wake_seconds)
        finally:
            self.state = "stopping"
            self._stopping = True
            await self._cleanup_workers()
            await self._cancel_supply_task()
            await self._cancel_post_commit_task()
            self._running = False

    async def stop(self) -> None:
        """Stop new claims, cancel workers, and release every unfinished token."""

        self._stopping = True
        self.state = "stopping"
        self._wake_event.set()
        await self._cleanup_workers()
        await self._cancel_supply_task()
        await self._cancel_post_commit_task()

    def status_payload(self) -> dict[str, Any]:
        """Return stable runtime diagnostics for API and event payloads."""

        snapshot = self._snapshot()
        return {
            "candidate_eval_state": self.state,
            "candidate_eval_workers": self.worker_count,
            "candidate_eval_in_flight": len(self._workers),
            "candidate_eval_pending": snapshot.pending_eval,
            "candidate_eval_backoff_until": self._backoff_until,
            "candidate_eval_last_error": self.last_error,
            "candidate_eval_last_batch_seconds": self.last_batch_seconds,
            "candidate_eval_last_cached": self.last_cached,
            "candidate_eval_last_rejected": self.last_rejected,
        }

    def _snapshot(self) -> CandidateEvalSnapshot:
        value = self.snapshot_provider()
        if isinstance(value, CandidateEvalSnapshot):
            return value
        return CandidateEvalSnapshot(
            available=int(value.get("available", 0)),
            target=int(value.get("target", 0)),
            pending_eval=int(value.get("pending_eval", 0)),
            evaluating=int(value.get("evaluating", 0)),
            evaluated_pending_admission=int(value.get("evaluated_pending_admission", 0)),
            admitted_pending_copy=int(value.get("admitted_pending_copy", 0)),
        )

    def _fill_open_slots(self) -> None:
        while not self._stopping and len(self._workers) < self.worker_count:
            snapshot = self._snapshot()
            if self._projected_inventory(snapshot) >= snapshot.target or snapshot.pending_eval <= 0:
                return
            claim = self.pipeline.claim_batch(limit=self.batch_size)
            if claim is None:
                return
            task = asyncio.create_task(
                self._evaluate_worker(claim),
                name=f"candidate_eval:{claim.token[:8]}",
            )
            self._workers[task] = claim

    async def _evaluate_worker(self, claim: Any) -> Any:
        profile = self.profile_provider()
        if inspect.isawaitable(profile):
            profile = await profile
        return await self.pipeline.evaluate_claim(claim, profile)

    def _admit_evaluated(self, snapshot: CandidateEvalSnapshot) -> None:
        if snapshot.evaluated_pending_admission <= 0:
            return
        admit = getattr(self.pipeline, "admit_evaluated", None)
        if not callable(admit):
            return
        admission_headroom = max(
            0,
            snapshot.target - snapshot.available - snapshot.admitted_pending_copy,
        )
        if admission_headroom <= 0:
            return
        result = admit(limit=admission_headroom)
        self.last_cached = int(result.get("cached", 0))
        self.last_rejected = int(result.get("rejected", 0))
        self._notify_admitted(self.last_cached)

    async def _commit_finished_workers(self) -> None:
        done = [task for task in self._workers if task.done()]
        for task in done:
            claim = self._workers.pop(task)
            try:
                outcome = task.result()
                snapshot = self._snapshot()
                admission_headroom = max(
                    0,
                    snapshot.target - snapshot.available - snapshot.admitted_pending_copy,
                )
                result = await self.pipeline.complete_claim(
                    outcome,
                    admission_limit=admission_headroom,
                )
            except asyncio.CancelledError:
                self._release_once(claim, reason="evaluation cancelled")
                continue
            except Exception as exc:
                self._release_once(claim, reason=str(exc))
                self._record_failure(exc)
                continue

            self.last_error = ""
            self.last_batch_seconds = float(getattr(outcome, "elapsed_seconds", 0.0) or 0.0)
            self.last_cached = int(result.get("cached", 0))
            self.last_rejected = int(result.get("rejected", 0))
            self._rate_limit_streak = 0
            self._transient_streak = 0
            if int(result.get("evaluated", 0)) > 0 and self.last_cached <= 0:
                self._zero_cache_streak += 1
            elif self.last_cached > 0:
                self._zero_cache_streak = 0
                self._no_progress_level = 0
            if self._zero_cache_streak >= 3:
                delay = _NO_PROGRESS_BACKOFF_SECONDS[
                    min(self._no_progress_level, len(_NO_PROGRESS_BACKOFF_SECONDS) - 1)
                ]
                self._no_progress_level += 1
                self._zero_cache_streak = 0
                self._backoff_until = max(self._backoff_until, self.time_fn() + delay)
                self._request_supply("candidate_eval_no_progress")
            if self.last_cached > 0:
                self._notify_admitted(self.last_cached)
                self._request_post_commit()

    def _notify_admitted(self, cached_count: int) -> None:
        if cached_count <= 0 or self.on_admitted is None:
            return
        try:
            self.on_admitted(cached_count)
        except Exception:
            logger.warning("candidate admission callback failed", exc_info=True)

    def _record_failure(self, exc: BaseException) -> None:
        self.last_error = str(exc)
        kind = classify_llm_failure_kind(exc)
        now = self.time_fn()
        if kind == "rate_limited":
            # 15s matches the scheduler's minimum useful retry cadence. Recalibrate
            # when provider/model cooldown behavior materially changes.
            delay = _RATE_LIMIT_BACKOFF_SECONDS[
                min(self._rate_limit_streak, len(_RATE_LIMIT_BACKOFF_SECONDS) - 1)
            ]
            self._rate_limit_streak += 1
            self._backoff_until = now + max(delay, self._retry_after_seconds(exc))
            return
        if kind in {"no_provider", "auth_failed"}:
            self._paused = True
            return
        if kind not in {"timeout", "connection", "server_error"}:
            logger.warning("candidate evaluation worker failed: %s", exc)
            return
        delay = _TRANSIENT_BACKOFF_SECONDS[
            min(self._transient_streak, len(_TRANSIENT_BACKOFF_SECONDS) - 1)
        ]
        self._transient_streak += 1
        self._backoff_until = now + delay
        logger.warning("candidate evaluation worker failed: %s", exc)

    async def _wait_for_activity(self, timeout: float) -> None:
        observed_generation = self._generation
        self._wake_event.clear()
        if observed_generation != self._generation:
            return
        wake_task = asyncio.create_task(self._wake_event.wait())
        waiters: set[asyncio.Task[Any]] = {wake_task, *self._workers.keys()}
        if self._supply_task is not None:
            waiters.add(self._supply_task)
        if self._post_commit_task is not None:
            waiters.add(self._post_commit_task)
        try:
            await asyncio.wait(
                waiters,
                timeout=max(0.0, float(timeout)),
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not wake_task.done():
                wake_task.cancel()
            await asyncio.gather(wake_task, return_exceptions=True)

    def _request_supply(self, reason: str) -> None:
        if self.supply_callback is None or self._supply_task is not None:
            return
        callback = self.supply_callback

        async def run() -> Any:
            result = callback(reason)
            if inspect.isawaitable(result):
                return await result
            return result

        self._supply_task = asyncio.create_task(run(), name="candidate_eval:supply")

    async def _settle_supply_task(self) -> None:
        task = self._supply_task
        if task is None or not task.done():
            return
        self._supply_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("candidate evaluation supply request failed: %s", exc)
            self.last_error = str(exc)

    async def _cancel_supply_task(self) -> None:
        task = self._supply_task
        self._supply_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _request_post_commit(self) -> None:
        callback = self.post_commit_callback
        if callback is None or self._stopping:
            return
        if self._post_commit_task is not None:
            self._post_commit_requested = True
            return

        async def run() -> Any:
            result = callback()
            if inspect.isawaitable(result):
                return await result
            return result

        self._post_commit_task = asyncio.create_task(
            run(),
            name="candidate_eval:post_commit",
        )

    async def _settle_post_commit_task(self) -> None:
        task = self._post_commit_task
        if task is None or not task.done():
            return
        self._post_commit_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("candidate evaluation post-commit hook failed: %s", exc)
            self.last_error = str(exc)
        rerun = self._post_commit_requested
        self._post_commit_requested = False
        if rerun and not self._stopping:
            self._request_post_commit()

    async def _cancel_post_commit_task(self) -> None:
        task = self._post_commit_task
        self._post_commit_task = None
        self._post_commit_requested = False
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _cleanup_workers(self) -> None:
        async with self._cleanup_lock:
            entries = list(self._workers.items())
            self._workers.clear()
            for task, _claim in entries:
                if not task.done():
                    task.cancel()
            if entries:
                await asyncio.gather(*(task for task, _claim in entries), return_exceptions=True)
            for _task, claim in entries:
                self._release_once(claim, reason="coordinator stopping")

    def _release_once(self, claim: Any, *, reason: str) -> None:
        token = str(getattr(claim, "token", ""))
        if token in self._released_tokens:
            return
        self._released_tokens.add(token)
        self.pipeline.release_claim(claim, reason=reason, increment_attempts=False)

    @staticmethod
    def _projected_inventory(snapshot: CandidateEvalSnapshot) -> int:
        return (
            max(0, snapshot.available)
            + max(0, snapshot.admitted_pending_copy)
            + max(0, snapshot.evaluated_pending_admission)
        )

    @staticmethod
    def _resume_notification(reason: str) -> bool:
        normalized = str(reason).strip().lower()
        return normalized.startswith(("config", "manual", "presence", "startup"))

    @staticmethod
    def _retry_after_seconds(exc: BaseException) -> float:
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            value = getattr(current, "retry_after", None)
            if isinstance(value, int | float) and value > 0:
                return float(value)
            current = current.__cause__ or current.__context__
        return 0.0
