"""Real-SQLite regressions for Task 20 review corrections."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config

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
from openbiliclaw.infrastructure.database.repositories import ProfileRevisionConflict
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs.orchestration import (
    WorkerDependencies,
    build_worker_runtime,
)
from openbiliclaw.infrastructure.jobs.tasks import (
    JobCancelledError,
    JobExecutionContext,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker


class Queue:
    def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None:
        del job_name, run_id, priority


@pytest.fixture
def database(tmp_path: Path) -> Iterator[tuple[Engine, sessionmaker[Session]]]:
    path = tmp_path / "task20-regressions.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(DatabaseSettings(url=f"sqlite:///{path}"))
    yield engine, session_factory
    engine.dispose()


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
