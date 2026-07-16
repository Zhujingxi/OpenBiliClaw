"""Real-SQLite regressions for Task 20 review corrections."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from threading import Event, Thread, current_thread
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event

from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind
from openbiliclaw.features.feed.domain import ContentItem, Interaction, InteractionKind
from openbiliclaw.features.feed.service import FeedbackService, FeedPolicy, FeedService
from openbiliclaw.features.profile.domain import ProfileDelta, ProfileSnapshot
from openbiliclaw.features.sources.domain import (
    SourceCapability,
    SourceId,
    SourceManifest,
    SourceOperation,
    SourceOperationSpec,
    SourceResultKind,
    SourceTransportKind,
)
from openbiliclaw.features.sources.registry import SourceRegistry
from openbiliclaw.features.system.service import SettingsService
from openbiliclaw.infrastructure.ai.tasks import (
    CandidateAssessmentOutput,
    CandidateBatchAssessmentOutput,
)
from openbiliclaw.infrastructure.ai.use_cases import TaskRunnerBatchAssessor
from openbiliclaw.infrastructure.database.base import DatabaseSettings, create_engine_and_session
from openbiliclaw.infrastructure.database.models import (
    CandidateAssessmentModel,
    ContentItemModel,
    FeedEntryModel,
    JobRunModel,
    ProfileConsumedEvidenceModel,
    ProfileRevisionModel,
)
from openbiliclaw.infrastructure.database.repositories import ProfileRevisionConflict
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs.orchestration import (
    WorkerDependencies,
    build_worker_runtime,
)
from openbiliclaw.infrastructure.jobs.queue import build_huey
from openbiliclaw.infrastructure.jobs.tasks import (
    HueyJobQueue,
    JobCancelledError,
    JobExecutionContext,
    JobRunStatus,
    JobService,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker


class Queue:
    def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None:
        del job_name, run_id, priority


class CancelBeforePersistenceContext(JobExecutionContext):
    """Deterministically let cancellation win immediately before the UoW guard."""

    def __init__(self, service: JobService, run_id: UUID) -> None:
        super().__init__(service, run_id)
        self._job_service = service
        self._cancelled = False

    def guard(self, uow: object) -> None:
        if not self._cancelled:
            self._job_service.cancel(self.run_id)
            self._cancelled = True
        super().guard(uow)


def _start_running_job(service: JobService, key: str) -> tuple[UUID, JobExecutionContext]:
    run = service.schedule("source_sync", idempotency_key=key)
    assert service.claim(run.id)
    return run.id, JobExecutionContext(service, run.id)


def _join(thread: Thread) -> None:
    thread.join(timeout=10)
    assert not thread.is_alive()


@pytest.fixture
def database(tmp_path: Path) -> Iterator[tuple[Engine, sessionmaker[Session]]]:
    path = tmp_path / "task20-regressions.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=f"sqlite:///{path}"))
    yield engine, session_factory
    engine.dispose()


def test_startup_republishes_dequeued_pending_huey_message_exactly_once(
    database: tuple[Engine, sessionmaker[Session]], tmp_path: Path
) -> None:
    """A transport dequeue before app claim must not strand durable pending work."""

    _engine, session_factory = database
    transport = build_huey(tmp_path / "startup-recovery-huey.db")
    effects: list[UUID] = []
    service: JobService

    @transport.task(name="source_sync")
    def execute_source_sync(run_id: str) -> None:
        resolved = UUID(run_id)
        if not service.claim(resolved):
            return
        effects.append(resolved)
        service.succeed(resolved)

    service = JobService(
        lambda: UnitOfWork(session_factory),
        queue=HueyJobQueue(tasks={"source_sync": execute_source_sync}),
    )
    run = service.schedule("source_sync", idempotency_key="dequeue-before-claim")
    dropped = transport.dequeue()
    assert dropped is not None
    assert service.inspect(run.id).status is JobRunStatus.PENDING
    assert service.inspect(run.id).dispatched_at is not None

    service.recover_interrupted()
    service.recover_interrupted()
    while (message := transport.dequeue()) is not None:
        transport.execute(message)

    assert effects == [run.id]
    assert service.inspect(run.id).status is JobRunStatus.SUCCEEDED


def test_sqlite_cancellation_write_order_first_rejects_guard_without_effect(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    """Cancellation holding the write order must make a later guard reject cleanly."""

    engine, session_factory = database
    cancel_repository_ready = Event()
    release_cancel_commit = Event()

    class PausingCancellationUnitOfWork(UnitOfWork):
        def commit(self) -> None:
            if current_thread().name == "task20-cancel-first":
                cancel_repository_ready.set()
                if not release_cancel_commit.wait(timeout=5):
                    raise AssertionError("timed out releasing cancellation commit")
            super().commit()

    service = JobService(lambda: PausingCancellationUnitOfWork(session_factory), queue=Queue())
    run_id, context = _start_running_job(service, "cancel-write-first")
    activity = ActivityEvent(
        source_id="local",
        kind=ActivityKind.CHAT_LEARNING,
        text="must not commit after cancellation wins",
    )
    guard_update_started = Event()
    guard_update_completed = Event()
    cancel_outcome: dict[str, object] = {}
    guard_outcome: dict[str, object] = {}

    def before_cursor_execute(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if current_thread().name == "task20-guard-second" and statement.lstrip().upper().startswith(
            "UPDATE JOB_RUNS"
        ):
            guard_update_started.set()

    def after_cursor_execute(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if current_thread().name == "task20-guard-second" and statement.lstrip().upper().startswith(
            "UPDATE JOB_RUNS"
        ):
            guard_update_completed.set()

    def cancel() -> None:
        try:
            cancel_outcome["snapshot"] = service.cancel(run_id)
        except BaseException as exc:  # pragma: no cover - asserted below
            cancel_outcome["error"] = exc

    def guarded_effect() -> None:
        try:
            with UnitOfWork(session_factory) as uow:
                context.guard(uow)
                uow.activities.add(activity)
                uow.commit()
        except BaseException as exc:
            guard_outcome["error"] = exc

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    cancel_thread = Thread(target=cancel, name="task20-cancel-first")
    guard_thread = Thread(target=guarded_effect, name="task20-guard-second")
    try:
        cancel_thread.start()
        assert cancel_repository_ready.wait(timeout=5)
        guard_thread.start()
        assert guard_update_started.wait(timeout=5)
        guard_completed_before_cancel_commit = guard_update_completed.wait(timeout=0.2)
        release_cancel_commit.set()
        _join(cancel_thread)
        _join(guard_thread)
    finally:
        release_cancel_commit.set()
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
        event.remove(engine, "after_cursor_execute", after_cursor_execute)

    assert not guard_completed_before_cancel_commit
    assert "error" not in cancel_outcome
    assert isinstance(guard_outcome.get("error"), JobCancelledError)
    with UnitOfWork(session_factory) as uow:
        assert uow.activities.list_all() == ()
    assert service.inspect(run_id).status is JobRunStatus.CANCELLED


def test_sqlite_guard_write_order_first_allows_effect_then_cancellation_without_lock_error(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    """A guard-held write order makes cancellation wait, then complete normally."""

    engine, session_factory = database
    service = JobService(lambda: UnitOfWork(session_factory), queue=Queue())
    run_id, context = _start_running_job(service, "guard-write-first")
    activity = ActivityEvent(
        source_id="local",
        kind=ActivityKind.CHAT_LEARNING,
        text="commits before cancellation",
    )
    cancel_update_started = Event()
    cancel_done = Event()
    cancel_outcome: dict[str, object] = {}

    def before_cursor_execute(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            current_thread().name == "task20-cancel-second"
            and statement.lstrip().upper().startswith("UPDATE JOB_RUNS")
        ):
            cancel_update_started.set()

    def cancel() -> None:
        try:
            cancel_outcome["snapshot"] = service.cancel(run_id)
        except BaseException as exc:  # pragma: no cover - asserted below
            cancel_outcome["error"] = exc
        finally:
            cancel_done.set()

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    cancel_thread = Thread(target=cancel, name="task20-cancel-second")
    try:
        with UnitOfWork(session_factory) as uow:
            context.guard(uow)
            uow.activities.add(activity)
            cancel_thread.start()
            assert cancel_update_started.wait(timeout=5)
            assert not cancel_done.wait(timeout=0.2)
            uow.commit()
        _join(cancel_thread)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert "error" not in cancel_outcome
    snapshot = cancel_outcome.get("snapshot")
    assert snapshot is not None
    assert snapshot.status is JobRunStatus.CANCELLED  # type: ignore[attr-defined]
    with UnitOfWork(session_factory) as uow:
        assert uow.activities.list_all() == (activity,)
    assert service.inspect(run_id).status is JobRunStatus.CANCELLED


def test_atomic_cancel_preserves_idempotent_terminal_and_missing_row_semantics(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, session_factory = database
    service = JobService(lambda: UnitOfWork(session_factory), queue=Queue())

    pending = service.schedule("cleanup", idempotency_key="cancel-idempotent")
    assert service.cancel(pending.id).status is JobRunStatus.CANCELLED
    assert service.cancel(pending.id).status is JobRunStatus.CANCELLED

    succeeded = service.schedule("cleanup", idempotency_key="cancel-succeeded")
    assert service.claim(succeeded.id)
    service.succeed(succeeded.id)
    assert service.cancel(succeeded.id).status is JobRunStatus.SUCCEEDED

    failed = service.schedule("cleanup", idempotency_key="cancel-failed")
    assert service.claim(failed.id)
    service.fail(failed.id, ValueError("classified without message persistence"))
    assert service.cancel(failed.id).status is JobRunStatus.FAILED

    with pytest.raises(LookupError, match="job run does not exist"):
        service.cancel(uuid4())


class BatchRunner:
    def __init__(self) -> None:
        self.batch_calls: list[tuple[UUID, ...]] = []
        self.profile_calls = 0

    async def run(self, spec: Any, raw_input: Any) -> Any:
        if spec.name == "candidate_batch_assessment":
            self.batch_calls.append(tuple(item.id for item in raw_input.content))
            return CandidateBatchAssessmentOutput(
                assessments=tuple(
                    CandidateAssessmentOutput(
                        content_id=item.id,
                        profile_revision=raw_input.profile.revision,
                        relevance=0.9,
                        quality=0.9,
                        novelty=0.9,
                        risk=0,
                        topics=(f"topic-{item.external_id}",),
                    )
                    for item in raw_input.content
                )
            )
        if spec.name == "profile_delta":
            self.profile_calls += 1
            return ProfileDelta(narrative="consumed without facets")
        raise AssertionError(spec.name)


class ListConnector:
    manifest = SourceManifest(
        source_id=SourceId.BILIBILI,
        display_name="Bilibili",
        capabilities=frozenset({SourceCapability.TRENDING_FEED}),
        operations=(
            SourceOperationSpec(
                operation=SourceOperation.TRENDING,
                capability=SourceCapability.TRENDING_FEED,
                result_kind=SourceResultKind.CONTENT,
                requires_auth=False,
                transport_kind=SourceTransportKind.DIRECT,
            ),
        ),
    )

    def __init__(self, items: tuple[ContentItem, ...]) -> None:
        self.items = items

    async def execute(
        self, operation: SourceOperation, query: str | None = None, limit: int = 20
    ) -> tuple[ContentItem, ...]:
        del operation, query
        return self.items[:limit]


@pytest.mark.asyncio
async def test_second_replenishment_excludes_durable_history_and_fills_with_new_content(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, session_factory = database
    items = tuple(
        ContentItem(
            source_id="bilibili",
            external_id=f"item-{index}",
            url=f"https://example.com/{index}",
            title=f"Item {index}",
        )
        for index in range(10)
    )
    runner = BatchRunner()
    with UnitOfWork(session_factory) as uow:
        uow.profiles.append(ProfileSnapshot(revision=0), expected_revision=None)
        uow.commit()
    service = FeedService(
        lambda: UnitOfWork(session_factory),
        connectors=(ListConnector(items),),
        assessor=TaskRunnerBatchAssessor(runner),  # type: ignore[arg-type]
        policy=FeedPolicy(low_watermark=1, high_watermark=2, max_per_source=10),
    )

    first = await service.replenish()
    for entry in first:
        FeedbackService(lambda: UnitOfWork(session_factory)).record(
            Interaction(content_id=entry.content_id, kind=InteractionKind.DISMISS)
        )
    second = await service.replenish()

    assert len(first) == len(second) == 2
    assert {entry.content_id for entry in first}.isdisjoint(entry.content_id for entry in second)
    assert set(runner.batch_calls[0]).isdisjoint(runner.batch_calls[1])


@pytest.mark.asyncio
async def test_narrative_only_profile_evidence_is_consumed_once_and_rollback_is_atomic(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, session_factory = database
    event = ActivityEvent(
        id=uuid4(),
        source_id="local",
        kind=ActivityKind.CHAT_LEARNING,
        text="durable evidence",
    )
    with UnitOfWork(session_factory) as uow:
        uow.activities.add(event)
        uow.commit()
    runner = BatchRunner()
    service, handlers = build_worker_runtime(
        WorkerDependencies(
            session_factory=session_factory,
            source_registry=SourceRegistry(()),
            task_runner=runner,  # type: ignore[arg-type]
            job_queue=Queue(),
        )
    )
    run = service.schedule("profile_projection", idempotency_key="consume-once")
    assert service.claim(run.id)
    await handlers["profile_projection"](run.id, JobExecutionContext(service, run.id))

    with UnitOfWork(session_factory) as uow:
        assert uow.profiles.consumed_evidence_ids() == frozenset({event.id})
    other = service.schedule("profile_projection", idempotency_key="consume-twice")
    assert service.claim(other.id)
    await handlers["profile_projection"](other.id, JobExecutionContext(service, other.id))
    assert runner.profile_calls == 1

    rolled_back = uuid4()
    with UnitOfWork(session_factory) as uow:
        uow.profiles.mark_evidence_consumed(frozenset({rolled_back}), profile_revision=0)
    with UnitOfWork(session_factory) as uow:
        assert rolled_back not in uow.profiles.consumed_evidence_ids()


def test_concurrent_projection_conflict_cannot_duplicate_consumed_ledger(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, session_factory = database
    event = ActivityEvent(
        source_id="local",
        kind=ActivityKind.CHAT_LEARNING,
        text="concurrent evidence",
    )
    with UnitOfWork(session_factory) as uow:
        uow.activities.add(event)
        uow.commit()

    first = UnitOfWork(session_factory)
    second = UnitOfWork(session_factory)
    try:
        first.__enter__()
        second.__enter__()
        assert first.profiles.latest() is None
        assert second.profiles.latest() is None
        first.profiles.append(ProfileSnapshot(revision=0), expected_revision=None)
        first.profiles.mark_evidence_consumed(frozenset({event.id}), profile_revision=0)
        first.commit()
        with pytest.raises(ProfileRevisionConflict):
            second.profiles.append(ProfileSnapshot(revision=0), expected_revision=None)
    finally:
        second.__exit__(None, None, None)
        first.__exit__(None, None, None)

    with UnitOfWork(session_factory) as uow:
        assert uow.profiles.consumed_evidence_ids() == frozenset({event.id})


class BlockingSource:
    manifest = SourceManifest(
        source_id=SourceId.BILIBILI,
        display_name="Bilibili",
        capabilities=frozenset({SourceCapability.BOOTSTRAP_IMPORT}),
        operations=(
            SourceOperationSpec(
                operation=SourceOperation.BOOTSTRAP_IMPORT,
                capability=SourceCapability.BOOTSTRAP_IMPORT,
                result_kind=SourceResultKind.ACTIVITY,
                requires_auth=False,
                transport_kind=SourceTransportKind.DIRECT,
            ),
        ),
    )

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(
        self, operation: SourceOperation, query: str | None = None, limit: int = 20
    ) -> tuple[ActivityEvent, ...]:
        del operation, query, limit
        self.started.set()
        await self.release.wait()
        return (
            ActivityEvent(
                source_id="bilibili",
                kind=ActivityKind.FAVORITE,
                title="must not persist after cancellation",
            ),
        )


@pytest.mark.asyncio
async def test_cancel_during_source_boundary_prevents_later_persistence_and_keeps_progress(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, session_factory = database
    connector = BlockingSource()
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    settings.update({"source_enabled": {"bilibili": True}})
    runner = BatchRunner()
    service, handlers = build_worker_runtime(
        WorkerDependencies(
            session_factory=session_factory,
            source_registry=SourceRegistry((connector,)),
            task_runner=runner,  # type: ignore[arg-type]
            job_queue=Queue(),
        )
    )
    run = service.schedule("source_sync", idempotency_key="cancel-mid-source")
    assert service.claim(run.id)
    execution = asyncio.create_task(
        handlers["source_sync"](run.id, JobExecutionContext(service, run.id))
    )
    await connector.started.wait()
    assert 0 < service.inspect(run.id).progress < 1

    service.cancel(run.id)
    connector.release.set()
    with pytest.raises(JobCancelledError):
        await execution

    with UnitOfWork(session_factory) as uow:
        assert uow.activities.list_all() == ()
    assert service.inspect(run.id).progress < 1


class BlockingProfileRunner(BatchRunner):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, spec: Any, raw_input: Any) -> Any:
        if spec.name == "profile_delta":
            self.started.set()
            await self.release.wait()
        return await super().run(spec, raw_input)


@pytest.mark.asyncio
async def test_cancel_during_model_boundary_prevents_profile_and_ledger_commit(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, session_factory = database
    event = ActivityEvent(
        source_id="local",
        kind=ActivityKind.CHAT_LEARNING,
        text="cancel model projection",
    )
    with UnitOfWork(session_factory) as uow:
        uow.activities.add(event)
        uow.commit()
    runner = BlockingProfileRunner()
    service, handlers = build_worker_runtime(
        WorkerDependencies(
            session_factory=session_factory,
            source_registry=SourceRegistry(()),
            task_runner=runner,  # type: ignore[arg-type]
            job_queue=Queue(),
        )
    )
    run = service.schedule("profile_projection", idempotency_key="cancel-mid-model")
    assert service.claim(run.id)
    execution = asyncio.create_task(
        handlers["profile_projection"](run.id, JobExecutionContext(service, run.id))
    )
    await runner.started.wait()
    assert 0 < service.inspect(run.id).progress < 1

    service.cancel(run.id)
    runner.release.set()
    with pytest.raises(JobCancelledError):
        await execution

    with UnitOfWork(session_factory) as uow:
        assert uow.profiles.latest() is None
        assert uow.profiles.consumed_evidence_ids() == frozenset()


class ImmediateActivitySource:
    manifest = SourceManifest(
        source_id=SourceId.BILIBILI,
        display_name="Bilibili",
        capabilities=frozenset({SourceCapability.BOOTSTRAP_IMPORT}),
        operations=(
            SourceOperationSpec(
                operation=SourceOperation.BOOTSTRAP_IMPORT,
                capability=SourceCapability.BOOTSTRAP_IMPORT,
                result_kind=SourceResultKind.ACTIVITY,
                requires_auth=False,
                transport_kind=SourceTransportKind.DIRECT,
            ),
        ),
    )

    async def execute(
        self, operation: SourceOperation, query: str | None = None, limit: int = 20
    ) -> tuple[ActivityEvent, ...]:
        del operation, query, limit
        return (
            ActivityEvent(
                source_id="bilibili",
                kind=ActivityKind.FAVORITE,
                title="cancel at atomic persistence guard",
            ),
        )


@pytest.mark.asyncio
async def test_source_cancellation_winning_at_atomic_guard_persists_no_activity(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, session_factory = database
    SettingsService(lambda: UnitOfWork(session_factory)).update(
        {"source_enabled": {"bilibili": True}}
    )
    service, handlers = build_worker_runtime(
        WorkerDependencies(
            session_factory=session_factory,
            source_registry=SourceRegistry((ImmediateActivitySource(),)),
            task_runner=BatchRunner(),  # type: ignore[arg-type]
            job_queue=Queue(),
        )
    )
    run = service.schedule("source_sync", idempotency_key="cancel-at-source-guard")
    assert service.claim(run.id)

    with pytest.raises(JobCancelledError):
        await handlers["source_sync"](run.id, CancelBeforePersistenceContext(service, run.id))

    with UnitOfWork(session_factory) as uow:
        assert uow.activities.list_all() == ()
    assert service.inspect(run.id).status is JobRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_profile_cancellation_winning_at_atomic_guard_persists_no_revision_or_ledger(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, session_factory = database
    event = ActivityEvent(
        source_id="local",
        kind=ActivityKind.CHAT_LEARNING,
        text="cancel at profile guard",
    )
    with UnitOfWork(session_factory) as uow:
        uow.activities.add(event)
        uow.commit()
    service, handlers = build_worker_runtime(
        WorkerDependencies(
            session_factory=session_factory,
            source_registry=SourceRegistry(()),
            task_runner=BatchRunner(),  # type: ignore[arg-type]
            job_queue=Queue(),
        )
    )
    run = service.schedule("profile_projection", idempotency_key="cancel-at-profile-guard")
    assert service.claim(run.id)

    with pytest.raises(JobCancelledError):
        await handlers["profile_projection"](
            run.id, CancelBeforePersistenceContext(service, run.id)
        )

    with session_factory() as session:
        assert session.query(ProfileRevisionModel).count() == 0
        assert session.query(ProfileConsumedEvidenceModel).count() == 0
    assert service.inspect(run.id).status is JobRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_feed_cancellation_winning_at_atomic_guard_persists_no_feed_graph(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, session_factory = database
    with UnitOfWork(session_factory) as uow:
        uow.profiles.append(ProfileSnapshot(revision=0), expected_revision=None)
        uow.commit()
    SettingsService(lambda: UnitOfWork(session_factory)).update(
        {"source_enabled": {"bilibili": True}, "feed_low_watermark": 1, "feed_high_watermark": 1}
    )
    item = ContentItem(
        source_id="bilibili",
        external_id="cancel-at-feed-guard",
        url="https://example.com/cancel-at-feed-guard",
        title="cancel at feed guard",
    )
    service, handlers = build_worker_runtime(
        WorkerDependencies(
            session_factory=session_factory,
            source_registry=SourceRegistry((ListConnector((item,)),)),
            task_runner=BatchRunner(),  # type: ignore[arg-type]
            job_queue=Queue(),
        )
    )
    run = service.schedule("feed_replenishment", idempotency_key="cancel-at-feed-guard")
    assert service.claim(run.id)

    with pytest.raises(JobCancelledError):
        await handlers["feed_replenishment"](
            run.id, CancelBeforePersistenceContext(service, run.id)
        )

    with session_factory() as session:
        assert session.query(ContentItemModel).count() == 0
        assert session.query(CandidateAssessmentModel).count() == 0
        assert session.query(FeedEntryModel).count() == 0
    assert service.inspect(run.id).status is JobRunStatus.CANCELLED


def test_cleanup_cancellation_winning_at_atomic_guard_preserves_terminal_history(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _engine, session_factory = database
    service, handlers = build_worker_runtime(
        WorkerDependencies(
            session_factory=session_factory,
            source_registry=SourceRegistry(()),
            task_runner=BatchRunner(),  # type: ignore[arg-type]
            job_queue=Queue(),
        )
    )
    old = service.schedule("source_sync", idempotency_key="old-terminal")
    assert service.claim(old.id)
    service.succeed(old.id)
    with session_factory() as session:
        row = session.get(JobRunModel, str(old.id))
        assert row is not None
        row.finished_at = datetime.now(UTC) - timedelta(days=31)
        session.commit()
    cleanup_run = service.schedule("cleanup", idempotency_key="cancel-at-cleanup-guard")
    assert service.claim(cleanup_run.id)

    with pytest.raises(JobCancelledError):
        handlers["cleanup"](cleanup_run.id, CancelBeforePersistenceContext(service, cleanup_run.id))

    assert service.inspect(old.id).status is JobRunStatus.SUCCEEDED
    assert service.inspect(cleanup_run.id).status is JobRunStatus.CANCELLED
