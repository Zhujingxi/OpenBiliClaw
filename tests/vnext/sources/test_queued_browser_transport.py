from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, update

from openbiliclaw.features.sources.domain import (
    BrowserOperationResult,
    SourceOperation,
    SourceTaskStatus,
)
from openbiliclaw.infrastructure.database.models import SourceTaskModel
from openbiliclaw.infrastructure.sources.browser_tasks import QueuedBrowserTransport

from .test_browser_tasks import task_context  # noqa: F401


class LateEnqueueFatalError(BaseException):
    """Test-only fatal enqueue outcome that must never escape a done callback."""


async def test_queue_transport_awaits_a_completed_typed_result(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    transport = QueuedBrowserTransport(
        service, "zhihu", timeout_seconds=1, poll_interval_seconds=0.001
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    claim = None
    while claim is None:
        claim = await asyncio.to_thread(service.claim, "zhihu")
        await asyncio.sleep(0)
    await asyncio.to_thread(
        service.complete,
        claim.id,
        claim.lease_token,
        BrowserOperationResult.validate_python(
            {
                "operation": claim.operation.value,
                "items": [{"content_id": "1", "content_type": "answer"}],
            }
        ),
    )
    assert await pending == [{"content_id": "1", "content_type": "answer"}]


async def test_queue_transport_has_a_bounded_wait(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    transport = QueuedBrowserTransport(
        service, "zhihu", timeout_seconds=0.01, poll_interval_seconds=0.001
    )
    with pytest.raises(TimeoutError):
        await transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    with session_factory() as session:
        rows = list(session.scalars(select(SourceTaskModel)))
    assert len(rows) == 1
    assert rows[0].status in {
        SourceTaskStatus.CANCELLED.value,
        SourceTaskStatus.ABANDONED.value,
    }
    assert service.claim("zhihu") is None

    retry_transport = QueuedBrowserTransport(
        service, "zhihu", timeout_seconds=1, poll_interval_seconds=0.001
    )
    retry = asyncio.create_task(
        retry_transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    claim = None
    while claim is None:
        claim = await asyncio.to_thread(service.claim, "zhihu")
        await asyncio.sleep(0)
    await asyncio.to_thread(
        service.complete,
        claim.id,
        claim.lease_token,
        BrowserOperationResult.validate_python(
            {
                "operation": claim.operation.value,
                "items": [{"content_id": "retry", "content_type": "answer"}],
            }
        ),
    )
    assert await retry
    with session_factory() as session:
        statuses = sorted(session.scalars(select(SourceTaskModel.status)))
    assert SourceTaskStatus.COMPLETED.value in statuses
    assert len(statuses) == 2
    assert set(statuses) & {
        SourceTaskStatus.CANCELLED.value,
        SourceTaskStatus.ABANDONED.value,
    }


async def test_explicit_async_cancellation_compensates_the_durable_task(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    transport = QueuedBrowserTransport(
        service, "zhihu", timeout_seconds=1, poll_interval_seconds=0.001
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    while True:
        with session_factory() as session:
            row = session.scalar(select(SourceTaskModel))
        if row is not None:
            break
        await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    with session_factory() as session:
        row = session.scalar(select(SourceTaskModel))
        assert row is not None
        assert row.status == SourceTaskStatus.CANCELLED.value
    assert service.claim("zhihu") is None


async def test_transient_cancel_failure_is_retried_until_durable_row_is_terminal(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    cancel_attempts = 0

    class FlakyCancelService:
        persistence_timeout_seconds = 0.02

        def enqueue(self, request: Any, *, task_id: Any, request_deadline_at: Any) -> Any:
            return service.enqueue(
                request, task_id=task_id, request_deadline_at=request_deadline_at
            )

        def cancel(self, task_id: Any) -> Any:
            nonlocal cancel_attempts
            cancel_attempts += 1
            if cancel_attempts == 1:
                raise RuntimeError("transient cancellation failure")
            return service.cancel(task_id)

        def snapshot(self, task_id: Any) -> Any:
            return service.snapshot(task_id)

    transport = QueuedBrowserTransport(
        FlakyCancelService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=1,
        poll_interval_seconds=0.001,
        cleanup_timeout_seconds=0.2,
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    while True:
        with session_factory() as session:
            row = session.scalar(select(SourceTaskModel))
        if row is not None:
            break
        await asyncio.sleep(0)

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert cancel_attempts == 2
    with session_factory() as session:
        row = session.scalar(select(SourceTaskModel))
    assert row is not None
    assert row.status == SourceTaskStatus.CANCELLED.value
    assert service.claim("zhihu") is None


async def test_cancellation_during_enqueue_leaves_only_a_terminal_row(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    started = threading.Event()
    release = threading.Event()

    class DelayedService:
        persistence_timeout_seconds = 0.2

        def enqueue(self, request: Any, *, task_id: Any, request_deadline_at: Any) -> Any:
            started.set()
            release.wait(timeout=1)
            return service.enqueue(
                request, task_id=task_id, request_deadline_at=request_deadline_at
            )

        def cancel(self, task_id: Any) -> Any:
            return service.cancel(task_id)

        def snapshot(self, task_id: Any) -> Any:
            return service.snapshot(task_id)

    transport = QueuedBrowserTransport(
        DelayedService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=1,
        poll_interval_seconds=0.001,
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    await asyncio.to_thread(started.wait, 1)
    pending.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    with session_factory() as session:
        rows = list(session.scalars(select(SourceTaskModel)))
    assert len(rows) == 1
    assert rows[0].status == SourceTaskStatus.CANCELLED.value


async def test_late_successful_enqueue_after_early_cancellation_is_terminalized(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    started = threading.Event()
    release = threading.Event()

    class DelayedService:
        persistence_timeout_seconds = 0.01

        def enqueue(self, request: Any, *, task_id: Any, request_deadline_at: Any) -> Any:
            started.set()
            release.wait(timeout=1)
            return service.enqueue(
                request, task_id=task_id, request_deadline_at=request_deadline_at
            )

        def cancel(self, task_id: Any) -> Any:
            return service.cancel(task_id)

        def snapshot(self, task_id: Any) -> Any:
            return service.snapshot(task_id)

    transport = QueuedBrowserTransport(
        DelayedService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=10,
        poll_interval_seconds=0.001,
        cleanup_timeout_seconds=0.02,
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    assert await asyncio.to_thread(started.wait, 1)
    pending.cancel()
    await asyncio.sleep(0.05)
    assert not pending.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending

    row = None
    for _ in range(200):
        with session_factory() as session:
            row = session.scalar(select(SourceTaskModel))
        if row is not None and row.status == SourceTaskStatus.CANCELLED.value:
            break
        await asyncio.sleep(0.001)
    assert row is not None
    assert row.status == SourceTaskStatus.CANCELLED.value
    assert service.claim("zhihu") is None


async def test_cancellation_structurally_drains_enqueue_before_parent_can_finish(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    started = threading.Event()
    release = threading.Event()

    class DelayedService:
        persistence_timeout_seconds = 0.01

        def enqueue(self, request: Any, *, task_id: Any, request_deadline_at: Any) -> Any:
            started.set()
            release.wait(timeout=1)
            return service.enqueue(
                request, task_id=task_id, request_deadline_at=request_deadline_at
            )

        def cancel(self, task_id: Any) -> Any:
            return service.cancel(task_id)

        def snapshot(self, task_id: Any) -> Any:
            return service.snapshot(task_id)

    transport = QueuedBrowserTransport(
        DelayedService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=10,
        poll_interval_seconds=0.001,
        cleanup_timeout_seconds=0.02,
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    assert await asyncio.to_thread(started.wait, 1)
    pending.cancel()
    await asyncio.sleep(0.05)
    pending.cancel()
    await asyncio.sleep(0)
    assert not pending.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    with session_factory() as session:
        row = session.scalar(select(SourceTaskModel))
    assert row is not None
    assert row.status == SourceTaskStatus.CANCELLED.value
    assert service.claim("zhihu") is None


async def test_late_cancellation_failure_logs_only_its_exception_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = threading.Event()
    release = threading.Event()
    cancel_called = threading.Event()
    secret = "late-cancel-secret-must-not-escape"

    class FailingLateCancelService:
        persistence_timeout_seconds = 0.01

        def enqueue(self, request: Any, *, task_id: Any, request_deadline_at: Any) -> Any:
            del request, request_deadline_at
            started.set()
            release.wait(timeout=1)
            return task_id

        def cancel(self, task_id: Any) -> None:
            del task_id
            cancel_called.set()
            raise RuntimeError(secret)

        def snapshot(self, task_id: Any) -> Any:
            del task_id
            raise AssertionError("snapshot must not run while enqueue is blocked")

    transport = QueuedBrowserTransport(
        FailingLateCancelService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=10,
        poll_interval_seconds=0.001,
        cleanup_timeout_seconds=0.02,
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    assert await asyncio.to_thread(started.wait, 1)
    pending.cancel()
    release.set()
    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        await pending
    assert await asyncio.to_thread(cancel_called.wait, 1)

    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert not hasattr(transport, "_late_cleanup_tasks")


async def test_enqueue_delay_beyond_operation_timeout_waits_for_terminal_row(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    started = threading.Event()
    release = threading.Event()

    class DelayedService:
        persistence_timeout_seconds = 0.02

        def enqueue(self, request: Any, *, task_id: Any, request_deadline_at: Any) -> Any:
            started.set()
            release.wait(timeout=1)
            return service.enqueue(
                request, task_id=task_id, request_deadline_at=request_deadline_at
            )

        def cancel(self, task_id: Any) -> Any:
            return service.cancel(task_id)

        def snapshot(self, task_id: Any) -> Any:
            return service.snapshot(task_id)

    transport = QueuedBrowserTransport(
        DelayedService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
        cleanup_timeout_seconds=0.03,
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    await asyncio.to_thread(started.wait, 1)
    await asyncio.sleep(0.05)
    assert not pending.done()
    release.set()
    with pytest.raises(TimeoutError):
        await pending

    for _ in range(100):
        with session_factory() as session:
            row = session.scalar(select(SourceTaskModel))
        if row is not None:
            break
        await asyncio.sleep(0.001)
    assert row is not None
    assert service.snapshot(row.id).status in {
        SourceTaskStatus.CANCELLED,
        SourceTaskStatus.ABANDONED,
    }
    assert service.claim("zhihu") is None


async def test_second_parent_cancellation_waits_for_blocked_cancel_cleanup(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, _, service = task_context
    cancel_started = threading.Event()
    cancel_release = threading.Event()

    class BlockingCancelService:
        persistence_timeout_seconds = 0.2

        def enqueue(self, request: Any, *, task_id: Any, request_deadline_at: Any) -> Any:
            return service.enqueue(
                request, task_id=task_id, request_deadline_at=request_deadline_at
            )

        def cancel(self, task_id: Any) -> Any:
            cancel_started.set()
            cancel_release.wait(timeout=1)
            return service.cancel(task_id)

        def snapshot(self, task_id: Any) -> Any:
            return service.snapshot(task_id)

    transport = QueuedBrowserTransport(
        BlockingCancelService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=1,
        poll_interval_seconds=0.001,
        cleanup_timeout_seconds=0.5,
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    while True:
        with session_factory() as session:
            row = session.scalar(select(SourceTaskModel))
        if row is not None:
            break
        await asyncio.sleep(0)

    pending.cancel()
    await asyncio.to_thread(cancel_started.wait, 1)
    pending.cancel()
    await asyncio.sleep(0.01)
    assert not pending.done()
    cancel_release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert service.snapshot(row.id).status is SourceTaskStatus.CANCELLED


async def test_cleanup_failure_preserves_original_timeout_and_deadline_excludes_claim(
    task_context: tuple[Any, Any, Any],  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_factory, _, service = task_context
    cancel_called = threading.Event()

    class FailingCancelService:
        persistence_timeout_seconds = 0.02

        def enqueue(self, request: Any, *, task_id: Any, request_deadline_at: Any) -> Any:
            return service.enqueue(
                request, task_id=task_id, request_deadline_at=request_deadline_at
            )

        def cancel(self, task_id: Any) -> Any:
            cancel_called.set()
            raise RuntimeError("cleanup-secret-must-not-be-logged")

        def snapshot(self, task_id: Any) -> Any:
            return service.snapshot(task_id)

    transport = QueuedBrowserTransport(
        FailingCancelService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
        cleanup_timeout_seconds=0.03,
    )
    with caplog.at_level(logging.WARNING), pytest.raises(TimeoutError):
        await transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)

    with session_factory() as session:
        row = session.scalar(select(SourceTaskModel))
    assert row is not None
    assert service.claim("zhihu") is None
    assert service.snapshot(row.id).status is SourceTaskStatus.ABANDONED
    assert cancel_called.is_set()
    assert "cleanup-secret-must-not-be-logged" not in caplog.text


async def test_late_enqueue_exception_is_drained_without_secret_or_loop_error(
    task_context: tuple[Any, Any, Any],  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, service = task_context
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    secret = "late-enqueue-secret-must-not-escape"
    loop_errors: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
    source_logger = logging.getLogger("openbiliclaw.infrastructure.sources.browser_tasks")
    monkeypatch.setattr(source_logger, "disabled", False)

    class LateFailingService:
        persistence_timeout_seconds = 0.02

        def enqueue(self, request: Any, *, task_id: Any, request_deadline_at: Any) -> Any:
            started.set()
            release.wait(timeout=1)
            finished.set()
            raise RuntimeError(secret)

        def cancel(self, task_id: Any) -> Any:
            return service.cancel(task_id)

        def snapshot(self, task_id: Any) -> Any:
            return service.snapshot(task_id)

    transport = QueuedBrowserTransport(
        LateFailingService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
        cleanup_timeout_seconds=0.02,
    )
    try:
        pending = asyncio.create_task(
            transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
        )
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.sleep(0.02)
        assert not pending.done()
        release.set()
        with caplog.at_level(logging.WARNING), pytest.raises(TimeoutError):
            await pending
        assert await asyncio.to_thread(finished.wait, 1)
    finally:
        release.set()
        loop.set_exception_handler(previous_handler)

    assert loop_errors == []
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_late_enqueue_base_exception_is_drained_without_secret_or_loop_error(
    task_context: tuple[Any, Any, Any],  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, service = task_context
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    secret = "late-base-exception-secret-must-not-escape"
    loop_errors: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
    source_logger = logging.getLogger("openbiliclaw.infrastructure.sources.browser_tasks")
    monkeypatch.setattr(source_logger, "disabled", False)

    class LateFatalService:
        persistence_timeout_seconds = 0.02

        def enqueue(self, request: Any, *, task_id: Any, request_deadline_at: Any) -> Any:
            started.set()
            release.wait(timeout=1)
            finished.set()
            raise LateEnqueueFatalError(secret)

        def cancel(self, task_id: Any) -> Any:
            return service.cancel(task_id)

        def snapshot(self, task_id: Any) -> Any:
            return service.snapshot(task_id)

    transport = QueuedBrowserTransport(
        LateFatalService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
        cleanup_timeout_seconds=0.02,
    )
    try:
        pending = asyncio.create_task(
            transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
        )
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.sleep(0.02)
        assert not pending.done()
        release.set()
        with caplog.at_level(logging.WARNING), pytest.raises(TimeoutError):
            await pending
        assert await asyncio.to_thread(finished.wait, 1)
    finally:
        release.set()
        loop.set_exception_handler(previous_handler)

    assert loop_errors == []
    assert secret not in caplog.text
    assert "LateEnqueueFatalError" in caplog.text


async def test_delayed_insert_and_blocked_worker_claim_use_database_deadline(
    task_context: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    session_factory, engine, service = task_context
    enqueue_started = threading.Event()
    release_enqueue = threading.Event()

    class DelayedService:
        persistence_timeout_seconds = 1.0

        def enqueue(self, request: Any, *, task_id: Any, request_deadline_at: Any) -> Any:
            enqueue_started.set()
            release_enqueue.wait(timeout=1)
            return service.enqueue(
                request,
                task_id=task_id,
                request_deadline_at=request_deadline_at,
            )

        def cancel(self, task_id: Any) -> Any:
            return service.cancel(task_id)

        def snapshot(self, task_id: Any) -> Any:
            return service.snapshot(task_id)

    transport = QueuedBrowserTransport(
        DelayedService(),  # type: ignore[arg-type]
        "zhihu",
        timeout_seconds=0.4,
        poll_interval_seconds=0.001,
        cleanup_timeout_seconds=0.3,
    )
    pending = asyncio.create_task(
        transport.fetch(operation=SourceOperation.SEARCH.value, query="python", limit=3)
    )
    assert await asyncio.to_thread(enqueue_started.wait, 1)
    await asyncio.sleep(0.05)
    release_enqueue.set()
    row = None
    for _ in range(200):
        with session_factory() as session:
            row = session.scalar(select(SourceTaskModel))
        if row is not None:
            break
        await asyncio.sleep(0.001)
    assert row is not None
    deadline = row.request_deadline_at.replace(tzinfo=UTC)
    claim_started = threading.Event()

    def claim() -> Any:
        claim_started.set()
        return service.claim("zhihu")

    with engine.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(
            update(SourceTaskModel)
            .where(SourceTaskModel.id == row.id)
            .values(updated_at=datetime.now(UTC))
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            outcome = pool.submit(claim)
            assert await asyncio.to_thread(claim_started.wait, 1)
            assert datetime.now(UTC) < deadline
            await asyncio.sleep(max(0.0, (deadline - datetime.now(UTC)).total_seconds()) + 0.05)
            assert not outcome.done()
            transaction.commit()
            assert await asyncio.to_thread(outcome.result, 1) is None

    with pytest.raises(TimeoutError):
        await pending
    assert service.claim("zhihu") is None
    assert service.snapshot(row.id).status in {
        SourceTaskStatus.CANCELLED,
        SourceTaskStatus.ABANDONED,
    }
