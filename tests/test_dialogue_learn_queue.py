"""Tests for the Phase 1 dialogue-learning serial queue.

Spec: docs/plans/2026-07-17-cognitive-profile-pipeline-spec.md §Design invariant 7.
Plan: docs/plans/2026-07-17-cognitive-profile-pipeline-plan.md Task 2.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openbiliclaw.soul.dialogue_learn_queue import DialogueLearnQueue


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

    queue = DialogueLearnQueue(handler)
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

    queue = DialogueLearnQueue(handler)
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
        }
    ]


@pytest.mark.asyncio
async def test_handler_exception_does_not_kill_worker() -> None:
    processed: list[str] = []

    async def handler(*, tag: str, **_: Any) -> None:
        if tag == "boom":
            raise ValueError("kaboom")
        processed.append(tag)

    queue = DialogueLearnQueue(handler)
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

    queue = DialogueLearnQueue(handler)
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

    queue = DialogueLearnQueue(handler)
    queue.start()
    for i in range(3):
        await queue.submit({"tag": str(i)})
    await queue.pause_and_drain()
    # Backlog drained; new submissions now rejected (paused-accepting).
    assert processed == ["0", "1", "2"]
    assert await queue.submit({"tag": "late"}) is False
    await queue.shutdown()


@pytest.mark.asyncio
async def test_worker_survives_registry_cancel_all() -> None:
    # The queue worker must NOT be in the runtime cancel_all registry.
    from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry

    async def handler(**_: Any) -> None:
        pass

    registry = BackgroundTaskRegistry()
    queue = DialogueLearnQueue(handler)
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

    old = DialogueLearnQueue(old_handler)
    old.start()
    await old.submit({"tag": "1"})

    # Hot reload: pause-drain old BEFORE building new.
    await old.pause_and_drain()
    new = DialogueLearnQueue(new_handler)
    new.start()
    # Build succeeded → stop old.
    await old.shutdown()
    await new.submit({"tag": "2"})
    await new.shutdown()

    assert log == ["old-1", "new-2"]
    assert old.worker_alive is False


@pytest.mark.asyncio
async def test_dialogue_respond_threads_scope_and_turn_id_via_queue() -> None:
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.soul.dialogue import SocraticDialogue

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

    queue = DialogueLearnQueue(_handler)
    queue.start()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,  # type: ignore[arg-type]
        llm_service=_StubService(),  # type: ignore[arg-type]
        session="popup",
        learn_queue=queue,
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
        }
    ]


@pytest.mark.asyncio
async def test_reload_failure_resumes_old_queue() -> None:
    processed: list[str] = []

    async def handler(*, tag: str, **_: Any) -> None:
        processed.append(tag)

    old = DialogueLearnQueue(handler)
    old.start()
    await old.submit({"tag": "before"})
    await old.pause_and_drain()  # drained before cancel_all

    # Simulate build failure → rollback: resume old queue.
    old.resume()
    assert await old.submit({"tag": "after"}) is True
    await old.shutdown()

    assert processed == ["before", "after"]
    assert old.worker_alive is False
