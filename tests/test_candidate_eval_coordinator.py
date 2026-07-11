from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from openbiliclaw.discovery.candidate_pipeline import CandidateEvalClaim, CandidateEvalOutcome
from openbiliclaw.runtime.candidate_eval import (
    CandidateEvalCoordinator,
    CandidateEvalSnapshot,
    effective_candidate_eval_workers,
)


@dataclass
class _FakeBatch:
    claim: CandidateEvalClaim
    future: asyncio.Future[dict[str, int]]


class _FakeStagedPipeline:
    def __init__(self, candidate_count: int) -> None:
        self.pending_eval = candidate_count
        self.available = 0
        self.started: list[_FakeBatch] = []
        self.released_tokens: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._started_event = asyncio.Event()

    def claim_batch(self, *, limit: int) -> CandidateEvalClaim | None:
        count = min(limit, self.pending_eval)
        if count <= 0:
            return None
        offset = sum(len(batch.claim.rows) for batch in self.started)
        token = f"claim-{len(self.started)}"
        rows = tuple({"id": offset + index + 1, "claim_token": token} for index in range(count))
        claim = CandidateEvalClaim(token=token, rows=rows, items=tuple(object() for _ in rows))  # type: ignore[arg-type]
        self.pending_eval -= count
        future: asyncio.Future[dict[str, int]] = asyncio.get_running_loop().create_future()
        self.started.append(_FakeBatch(claim=claim, future=future))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self._started_event.set()
        return claim

    async def evaluate_claim(self, claim: CandidateEvalClaim, profile: Any) -> CandidateEvalOutcome:
        batch = next(batch for batch in self.started if batch.claim.token == claim.token)
        await batch.future
        return CandidateEvalOutcome(
            claim=claim, scores=(0.9,) * len(claim.rows), elapsed_seconds=0.1
        )

    async def complete_claim(self, outcome: CandidateEvalOutcome) -> dict[str, int]:
        batch = next(batch for batch in self.started if batch.claim.token == outcome.claim.token)
        result = batch.future.result()
        self.in_flight -= 1
        self.available += result.get("cached", 0)
        return result

    def release_claim(
        self,
        claim: CandidateEvalClaim,
        *,
        reason: str,
        increment_attempts: bool = False,
    ) -> int:
        if claim.token in self.released_tokens:
            return 0
        self.released_tokens.append(claim.token)
        self.pending_eval += len(claim.rows)
        self.in_flight = max(0, self.in_flight - 1)
        return len(claim.rows)

    async def wait_for_started(self, count: int) -> None:
        async with asyncio.timeout(2):
            while len(self.started) < count:
                self._started_event.clear()
                await self._started_event.wait()

    def finish(self, index: int, *, cached: int) -> None:
        batch = self.started[index]
        if not batch.future.done():
            batch.future.set_result(
                {
                    "evaluated": len(batch.claim.rows),
                    "cached": cached,
                    "rejected": len(batch.claim.rows) - cached,
                    "stale": 0,
                }
            )


def _coordinator(
    pipeline: _FakeStagedPipeline,
    *,
    worker_count: int = 3,
    target: int = 600,
) -> CandidateEvalCoordinator:
    return CandidateEvalCoordinator(
        pipeline=pipeline,  # type: ignore[arg-type]
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=pipeline.available,
            target=target,
            pending_eval=pipeline.pending_eval,
            evaluating=pipeline.in_flight * 30,
            evaluated=0,
        ),
        profile_provider=lambda: object(),
        worker_count=worker_count,
        batch_size=30,
        safety_wake_seconds=0.05,
    )


def test_effective_candidate_eval_workers_reserves_one_llm_slot() -> None:
    assert effective_candidate_eval_workers(3, 4) == 3
    assert effective_candidate_eval_workers(3, 3) == 2
    assert effective_candidate_eval_workers(8, 1) == 1
    assert effective_candidate_eval_workers(0, 99) == 1


@pytest.mark.asyncio
async def test_three_workers_refill_fast_slot_without_waiting_for_slow_slots() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=120)
    coordinator = _coordinator(pipeline)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("test")
    await pipeline.wait_for_started(3)
    assert pipeline.max_in_flight == 3

    pipeline.finish(0, cached=10)
    await pipeline.wait_for_started(4)

    assert pipeline.started[1].future.done() is False
    assert pipeline.started[2].future.done() is False
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_target_stops_new_claims_and_stop_releases_in_flight() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=90)
    coordinator = _coordinator(pipeline, target=10)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("start")
    await pipeline.wait_for_started(3)
    pipeline.finish(0, cached=10)
    async with asyncio.timeout(2):
        while pipeline.available < 10:
            await asyncio.sleep(0)
    await asyncio.sleep(0.08)

    assert len(pipeline.started) == 3
    await coordinator.stop()
    await task
    assert set(pipeline.released_tokens) == {"claim-1", "claim-2"}
    assert pipeline.pending_eval == 60


@pytest.mark.asyncio
async def test_notify_at_idle_boundary_is_not_lost() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=0)
    coordinator = _coordinator(pipeline, worker_count=1)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("empty")
    await asyncio.sleep(0)
    pipeline.pending_eval = 30
    coordinator.notify("candidate_enqueued:test")

    await pipeline.wait_for_started(1)
    await coordinator.stop()
    await task
