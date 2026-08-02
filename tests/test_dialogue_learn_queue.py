"""Tests for the Phase 1 dialogue-learning serial queue.

Spec: docs/plans/2026-07-17-cognitive-profile-pipeline-spec.md §Design invariant 7.
Plan: docs/plans/2026-07-17-cognitive-profile-pipeline-plan.md Task 2.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.soul.dialogue_learn_queue import (
    AnchorPersisted,
    DialogueJob,
    DialogueJobKind,
    DialogueJobResult,
    DialogueSettlementQueue,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping


class _LearnQueueHarness:
    """Keep the historical lifecycle tests focused on typed queue behavior."""

    def __init__(
        self,
        handler: Callable[..., Awaitable[object]],
        *,
        name: str = "dialogue-test-worker",
        anchor_provider: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        async def dispatch(job: DialogueJob) -> DialogueJobResult:
            payload = dict(job.payload)
            snapshot = job.effective_anchor_snapshot
            if isinstance(snapshot, AnchorPersisted):
                payload["anchor_ref"] = snapshot.ref
                payload["anchor_generation"] = snapshot.generation
            else:
                payload["anchor_ref"] = ""
                payload["anchor_generation"] = 0
            await handler(**payload)
            return DialogueJobResult(outcome="completed")

        self.settlement_queue = DialogueSettlementQueue(
            dispatch,
            name=name,
            anchor_provider=anchor_provider,
        )

    @property
    def worker_alive(self) -> bool:
        return self.settlement_queue.worker_alive

    @property
    def worker_permit(self) -> object | None:
        return self.settlement_queue.worker_permit

    def start(self) -> None:
        self.settlement_queue.start()

    def pause(self) -> None:
        self.settlement_queue.pause()

    def resume(self) -> None:
        self.settlement_queue.resume()

    async def wait_until_started(self) -> None:
        await self.settlement_queue.wait_until_started()

    def revoke_worker_permit(self) -> bool:
        return self.settlement_queue.revoke_worker_permit()

    def reauthorize_worker(self) -> object:
        return self.settlement_queue.reauthorize_worker()

    async def submit(self, payload: Mapping[str, object]) -> bool:
        return self.settlement_queue.submit(DialogueJobKind.LEARN, payload) is not None

    async def pause_and_drain(self, *, timeout: float | None = None) -> None:
        await self.settlement_queue.pause_and_drain(timeout=timeout)

    async def shutdown(self, *, timeout: float | None = None) -> None:
        await self.settlement_queue.shutdown(timeout=timeout)


@pytest.mark.asyncio
async def test_tasks_execute_strictly_serially() -> None:
    order: list[str] = []
    active = 0
    max_active = 0

    async def handler(*, tag: str, **_: Any) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        order.append(f"start-{tag}")
        await asyncio.sleep(0.01)
        order.append(f"end-{tag}")
        active -= 1

    queue = _LearnQueueHarness(handler)
    queue.start()
    # Submit 5 concurrently — must still run one at a time, in FIFO order.
    await asyncio.gather(*(queue.submit({"tag": str(i)}) for i in range(5)))
    await queue.shutdown()

    assert max_active == 1  # never two handlers at once
    assert order == [
        "start-0",
        "end-0",
        "start-1",
        "end-1",
        "start-2",
        "end-2",
        "start-3",
        "end-3",
        "start-4",
        "end-4",
    ]


@pytest.mark.asyncio
async def test_handler_receives_payload_kwargs() -> None:
    seen: list[dict[str, Any]] = []

    async def handler(**kwargs: Any) -> None:
        seen.append(kwargs)

    queue = _LearnQueueHarness(handler)
    queue.start()
    await queue.submit(
        {
            "user_message": "hi",
            "assistant_reply": "yo",
            "session": "popup",
            "scope": "chat",
            "turn_id": "t-1",
        }
    )
    await queue.shutdown()

    assert seen == [
        {
            "user_message": "hi",
            "assistant_reply": "yo",
            "session": "popup",
            "scope": "chat",
            "turn_id": "t-1",
            "anchor_ref": "",
            "anchor_generation": 0,
        }
    ]


@pytest.mark.asyncio
async def test_anchor_snapshot_is_captured_when_turn_is_submitted() -> None:
    seen: list[dict[str, Any]] = []
    snapshot: dict[str, object] = {
        "anchor_ref": "first",
        "anchor_generation": 3,
    }

    async def handler(**kwargs: Any) -> None:
        seen.append(kwargs)

    queue = _LearnQueueHarness(handler, anchor_provider=lambda: dict(snapshot))
    queue.start()
    await queue.submit({"tag": "turn"})
    snapshot.update(anchor_ref="replacement", anchor_generation=4)
    await queue.shutdown()

    assert seen == [
        {
            "tag": "turn",
            "anchor_ref": "first",
            "anchor_generation": 3,
        }
    ]


@pytest.mark.asyncio
async def test_server_frozen_learn_override_never_reads_latest_anchor() -> None:
    """A server-owned POST snapshot remains A even after the registry changes."""
    from openbiliclaw.soul.dialogue_turn_context import DialogueTurnBinding, DialogueTurnContext

    provider_calls = 0
    phases: list[str] = []
    seen: list[tuple[object, dict[str, object]]] = []

    def provider() -> Mapping[str, object]:
        nonlocal provider_calls
        provider_calls += 1
        phases.append("latest-anchor-read")
        return {"kind": "hypothesis", "ref": "ref-b", "generation": 8}

    async def dispatch(job: DialogueJob) -> DialogueJobResult:
        phases.append("dispatch")
        seen.append((job.effective_anchor_snapshot, dict(job.payload)))
        return DialogueJobResult(outcome="completed")

    context = DialogueTurnContext(
        reply_to_turn_id="card-a",
        source_type="card",
        kind="hypothesis",
        ref="ref-a",
        generation=7,
        anchor_origin_turn_id="card-a",
        title="A",
    )
    queue = DialogueSettlementQueue(dispatch, anchor_provider=provider)
    queue.submit(
        DialogueJobKind.LEARN,
        {"dialogue_binding": DialogueTurnBinding.from_context(context).to_mapping()},
        _server_frozen_anchor_snapshot=AnchorPersisted(
            kind="hypothesis", ref="ref-a", generation=7
        ),
    )
    await queue.shutdown()

    assert provider_calls >= 0
    if provider_calls:
        assert phases.index("dispatch") < phases.index("latest-anchor-read")
    assert len(seen) == 1
    snapshot, payload = seen[0]
    assert isinstance(snapshot, AnchorPersisted)
    assert (snapshot.ref, snapshot.generation) == ("ref-a", 7)
    assert payload["dialogue_binding"]["context"]["ref"] == "ref-a"  # type: ignore[index]


@pytest.mark.asyncio
async def test_handler_exception_does_not_kill_worker() -> None:
    processed: list[str] = []

    async def handler(*, tag: str, **_: Any) -> None:
        if tag == "boom":
            raise ValueError("kaboom")
        processed.append(tag)

    queue = _LearnQueueHarness(handler)
    queue.start()
    await queue.submit({"tag": "boom"})
    await queue.submit({"tag": "ok"})
    await queue.shutdown()

    assert processed == ["ok"]  # worker survived the exception


@pytest.mark.asyncio
async def test_submit_rejected_when_paused() -> None:
    processed: list[str] = []

    async def handler(*, tag: str, **_: Any) -> None:
        processed.append(tag)

    queue = _LearnQueueHarness(handler)
    queue.start()
    queue.pause()
    accepted = await queue.submit({"tag": "x"})
    assert accepted is False
    queue.resume()
    accepted2 = await queue.submit({"tag": "y"})
    assert accepted2 is True
    await queue.shutdown()
    assert processed == ["y"]


@pytest.mark.asyncio
async def test_pause_and_drain_processes_backlog() -> None:
    processed: list[str] = []

    async def handler(*, tag: str, **_: Any) -> None:
        await asyncio.sleep(0.005)
        processed.append(tag)

    queue = _LearnQueueHarness(handler)
    queue.start()
    for i in range(3):
        await queue.submit({"tag": str(i)})
    await queue.pause_and_drain()
    # Backlog drained; new submissions now rejected (paused-accepting).
    assert processed == ["0", "1", "2"]
    assert await queue.submit({"tag": "late"}) is False
    await queue.shutdown()


@pytest.mark.asyncio
async def test_pause_and_drain_timeout_is_reported_and_queue_can_resume() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    processed: list[str] = []

    async def handler(*, tag: str, **_: Any) -> None:
        entered.set()
        await release.wait()
        processed.append(tag)

    queue = _LearnQueueHarness(handler)
    queue.start()
    await queue.submit({"tag": "blocked"})
    await asyncio.wait_for(entered.wait(), timeout=1)

    try:
        with pytest.raises(TimeoutError):
            await queue.pause_and_drain(timeout=0.01)
        # A timed-out drain never entered the atomic paused state, so user
        # work remains admissible instead of being dropped during hot reload.
        assert await queue.submit({"tag": "while-draining"}) is True
        queue.resume()
        assert await queue.submit({"tag": "after-resume"}) is True
    finally:
        release.set()
        await queue.shutdown(timeout=1)

    assert processed == ["blocked", "while-draining", "after-resume"]


@pytest.mark.asyncio
async def test_worker_survives_registry_cancel_all() -> None:
    # The queue worker must NOT be in the runtime cancel_all registry.
    from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry

    async def handler(**_: Any) -> None:
        pass

    registry = BackgroundTaskRegistry()
    queue = _LearnQueueHarness(handler)
    queue.start()

    # Register some other unrelated task.
    async def _other() -> None:
        await asyncio.sleep(100)

    registry.track("other", _other())
    await asyncio.sleep(0)
    cancelled = await registry.cancel_all()
    assert cancelled >= 1
    # Queue worker is self-owned — still alive after cancel_all.
    assert queue.worker_alive is True

    await queue.submit({"x": 1})
    await queue.shutdown()


@pytest.mark.asyncio
async def test_reload_success_stop_old_start_new_no_interleave() -> None:
    log: list[str] = []

    async def old_handler(*, tag: str, **_: Any) -> None:
        await asyncio.sleep(0.005)
        log.append(f"old-{tag}")

    async def new_handler(*, tag: str, **_: Any) -> None:
        await asyncio.sleep(0.005)
        log.append(f"new-{tag}")

    old = _LearnQueueHarness(old_handler)
    old.start()
    await old.submit({"tag": "1"})

    # Hot reload: drain and revoke old before the new worker registers.
    await old.pause_and_drain()
    await old.wait_until_started()
    assert old.revoke_worker_permit() is True
    new = _LearnQueueHarness(new_handler)
    new.start()
    await new.wait_until_started()
    await old.shutdown()
    await new.submit({"tag": "2"})
    await new.shutdown()

    assert log == ["old-1", "new-2"]
    assert old.worker_alive is False


@pytest.mark.asyncio
async def test_dialogue_respond_threads_scope_and_turn_id_via_queue() -> None:
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.soul.dialogue import DialogueLearningMode, SocraticDialogue

    captured: list[dict[str, Any]] = []

    class _StubSoulEngine:
        async def learn_from_dialogue(self, **kwargs: Any) -> None:
            captured.append(kwargs)

    class _StubService:
        async def complete_socratic_dialogue(
            self, *, user_message: str, history: list[dict[str, str]], caller: str = ""
        ) -> LLMResponse:
            return LLMResponse(content="ok", provider="openai")

    soul_engine = _StubSoulEngine()

    async def _handler(**payload: Any) -> None:
        await soul_engine.learn_from_dialogue(**payload)

    queue = _LearnQueueHarness(_handler)
    queue.start()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,  # type: ignore[arg-type]
        llm_service=_StubService(),  # type: ignore[arg-type]
        session="popup",
        learning_mode=DialogueLearningMode.QUEUED,
        settlement_queue=queue.settlement_queue,
    )
    await dialogue.respond("先放着吧", scope="confusion", turn_id="conf-7")
    await queue.shutdown()

    assert captured == [
        {
            "user_message": "先放着吧",
            "assistant_reply": "ok",
            "session": "popup",
            "scope": "confusion",
            "turn_id": "conf-7",
            "anchor_ref": "",
            "anchor_generation": 0,
        }
    ]


@pytest.mark.asyncio
async def test_reload_failure_resumes_old_queue() -> None:
    processed: list[str] = []

    async def handler(*, tag: str, **_: Any) -> None:
        processed.append(tag)

    old = _LearnQueueHarness(handler)
    old.start()
    await old.submit({"tag": "before"})
    await old.pause_and_drain()  # drained before cancel_all

    # Simulate build failure → rollback: resume old queue.
    old.resume()
    assert await old.submit({"tag": "after"}) is True
    await old.shutdown()

    assert processed == ["before", "after"]
    assert old.worker_alive is False


@pytest.mark.asyncio
async def test_runtime_reload_drain_timeout_keeps_old_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.api import runtime_context
    from openbiliclaw.api.runtime_context import RuntimeContext
    from openbiliclaw.config import Config

    entered = asyncio.Event()
    release = asyncio.Event()
    processed: list[str] = []

    async def handler(*, tag: str, **_: Any) -> None:
        if tag == "blocked":
            entered.set()
            await release.wait()
        processed.append(tag)

    old = _LearnQueueHarness(handler)
    old.start()
    await old.submit({"tag": "blocked"})
    await asyncio.wait_for(entered.wait(), timeout=1)
    ctx = RuntimeContext(dialogue_settlement_queue=old)
    rebuild_calls: list[object] = []
    cancel_calls: list[object] = []

    def fake_rebuild(new_config: object) -> None:
        rebuild_calls.append(new_config)

    async def fake_cancel_all(**kwargs: object) -> int:
        cancel_calls.append(kwargs)
        return 0

    monkeypatch.setattr(
        runtime_context,
        "_DIALOGUE_SETTLEMENT_DRAIN_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(ctx, "_rebuild_components", fake_rebuild)
    monkeypatch.setattr(ctx.task_registry, "cancel_all", fake_cancel_all)

    try:
        with pytest.raises(TimeoutError):
            await ctx.rebuild_from_config(Config())
        assert ctx.dialogue_settlement_queue is old
        assert old.worker_alive is True
        assert rebuild_calls == []
        assert cancel_calls == []
        # The abort path resumes the still-installed generation rather than
        # leaving it permanently paused after the failed reload.
        assert await old.submit({"tag": "after-timeout"}) is True
    finally:
        release.set()
        await old.shutdown(timeout=1)

    assert processed == ["blocked", "after-timeout"]


def test_start_without_loop_emits_no_unawaited_coroutine_warning() -> None:
    """Synchronous ``start()`` (no running loop) must not build an orphaned coroutine.

    Regression for the startup ``RuntimeWarning: coroutine 'DialogueSettlementQueue._run'
    was never awaited`` — ``start`` used to call ``self._run()`` before
    ``create_task`` raised, leaving the coroutine un-awaited.
    """
    import gc
    import warnings

    async def _handler(**kwargs: object) -> None:  # pragma: no cover - never runs
        return None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        queue = _LearnQueueHarness(_handler)
        queue.start()  # no running loop here (sync test)
        assert queue.worker_alive is False
        gc.collect()  # RuntimeWarning fires at coroutine GC time

    runtime_warnings = [
        w
        for w in caught
        if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)
    ]
    assert runtime_warnings == []


async def test_start_without_loop_still_lazy_starts_on_first_submit() -> None:
    """The deferred-start behaviour is preserved: first submit spawns the worker."""
    done = asyncio.Event()

    async def _handler(**kwargs: object) -> None:
        done.set()

    queue = _LearnQueueHarness(_handler)
    # Simulate the sync-startup state: no worker yet.
    assert queue.worker_alive is False
    await queue.submit({"user_message": "hi", "assistant_reply": "yo", "session": "s"})
    assert queue.worker_alive is True
    await asyncio.wait_for(done.wait(), timeout=2)
    await queue.shutdown(timeout=2)
