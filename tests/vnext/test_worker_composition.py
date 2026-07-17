"""Production-composition smoke tests with real SQLite and mocked external boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config

from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind
from openbiliclaw.features.feed.domain import ContentItem
from openbiliclaw.features.profile.domain import ProfileDelta, ProfileFacet
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
from openbiliclaw.infrastructure.database.base import DatabaseSettings, create_engine_and_session
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs.orchestration import (
    WorkerDependencies,
    build_worker_runtime,
)
from openbiliclaw.infrastructure.jobs.tasks import JobExecutionContext
from openbiliclaw.infrastructure.jobs.worker import (
    MissingSourceConfigurationError,
    build_default_source_registry,
)

if TYPE_CHECKING:
    from pathlib import Path

EVENT_ID = UUID("00000000-0000-0000-0000-000000000091")
RUN_ID = UUID("00000000-0000-0000-0000-000000000092")


class MockConnector:
    manifest = SourceManifest(
        source_id=SourceId.BILIBILI,
        display_name="Bilibili",
        capabilities=frozenset({SourceCapability.BOOTSTRAP_IMPORT, SourceCapability.TRENDING_FEED}),
        operations=(
            SourceOperationSpec(
                operation=SourceOperation.BOOTSTRAP_IMPORT,
                capability=SourceCapability.BOOTSTRAP_IMPORT,
                result_kind=SourceResultKind.ACTIVITY,
                requires_auth=False,
                transport_kind=SourceTransportKind.DIRECT,
            ),
            SourceOperationSpec(
                operation=SourceOperation.TRENDING,
                capability=SourceCapability.TRENDING_FEED,
                result_kind=SourceResultKind.CONTENT,
                requires_auth=False,
                transport_kind=SourceTransportKind.DIRECT,
            ),
        ),
    )

    async def execute(
        self, operation: SourceOperation, query: str | None = None, limit: int = 20
    ) -> tuple[ActivityEvent, ...] | tuple[ContentItem, ...]:
        del query, limit
        if operation is SourceOperation.BOOTSTRAP_IMPORT:
            return (
                ActivityEvent(
                    id=EVENT_ID,
                    source_id="bilibili",
                    kind=ActivityKind.FAVORITE,
                    title="Python architecture",
                ),
            )
        return (
            ContentItem(
                source_id="bilibili",
                external_id="BV-worker-smoke",
                url="https://www.bilibili.com/video/BV-worker-smoke",
                title="Typed Python architecture",
            ),
        )


class MockTaskRunner:
    async def run(self, spec: Any, raw_input: Any) -> Any:
        if spec.name == "profile_delta":
            return ProfileDelta(
                upserts=(
                    ProfileFacet(
                        name="interests",
                        value="Python",
                        weight=0.8,
                        confidence=0.9,
                        evidence_ids=(raw_input.evidence[0].id,),
                    ),
                )
            )
        if spec.name == "candidate_batch_assessment":
            return CandidateBatchAssessmentOutput(
                assessments=tuple(
                    CandidateAssessmentOutput(
                        content_id=item.id,
                        profile_revision=raw_input.profile.revision,
                        relevance=0.9,
                        quality=0.9,
                        novelty=0.9,
                        risk=0,
                        topics=("python",),
                    )
                    for item in raw_input.content
                )
            )
        raise AssertionError(f"unexpected task: {spec.name}")


class Queue:
    def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None:
        del job_name, run_id, priority


@pytest.fixture
def runtime(tmp_path: Path) -> tuple[Any, Any, Any]:
    database = tmp_path / "vnext.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=f"sqlite:///{database}")
    )
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    current = settings.get()
    settings.update(
        {
            "feed": {"low_watermark": 1, "high_watermark": 1},
            "sources": {
                "enabled": {**current.sources.enabled, "bilibili": True},
            },
        }
    )
    service, handlers = build_worker_runtime(
        WorkerDependencies(
            session_factory=session_factory,
            source_registry=SourceRegistry((MockConnector(),)),
            task_runner=MockTaskRunner(),  # type: ignore[arg-type]
            job_queue=Queue(),
        )
    )
    yield session_factory, service, handlers
    engine.dispose()


@pytest.mark.asyncio
async def test_all_four_production_handlers_execute_real_use_cases(
    runtime: tuple[Any, Any, Any],
) -> None:
    session_factory, service, handlers = runtime

    for index, name in enumerate(
        ("source_sync", "profile_projection", "feed_replenishment", "cleanup")
    ):
        run = service.schedule(name, idempotency_key=f"smoke:{index}")
        assert service.claim(run.id)
        result = handlers[name](run.id, JobExecutionContext(service, run.id))
        if result is not None:
            await result
        assert service.inspect(run.id).progress > 0

    with UnitOfWork(session_factory) as uow:
        assert len(uow.activities.list_all()) == 1
        assert uow.profiles.latest() is not None
        assert uow.feed.unseen_count() == 1


@pytest.mark.asyncio
async def test_long_lived_worker_resolves_a_fresh_source_registry_for_each_job(
    tmp_path: Path,
) -> None:
    database = tmp_path / "refreshing-worker.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=f"sqlite:///{database}")
    )
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    current = settings.get()
    settings.update(
        {
            "sources": {
                "enabled": {**current.sources.enabled, "bilibili": True},
            },
        }
    )

    class RefreshingConnector(MockConnector):
        def __init__(self, event_id: UUID) -> None:
            self._event_id = event_id

        async def execute(
            self, operation: SourceOperation, query: str | None = None, limit: int = 20
        ) -> tuple[ActivityEvent, ...] | tuple[ContentItem, ...]:
            if operation is SourceOperation.BOOTSTRAP_IMPORT:
                return (
                    ActivityEvent(
                        id=self._event_id,
                        source_id="bilibili",
                        kind=ActivityKind.FAVORITE,
                        title=str(self._event_id),
                    ),
                )
            return await super().execute(operation, query=query, limit=limit)

    connectors = [RefreshingConnector(uuid4()), RefreshingConnector(uuid4())]
    provider_calls = 0

    def registry_provider() -> SourceRegistry:
        nonlocal provider_calls
        selected = connectors[min(provider_calls, len(connectors) - 1)]
        provider_calls += 1
        return SourceRegistry((selected,))

    service, handlers = build_worker_runtime(
        WorkerDependencies(
            session_factory=session_factory,
            source_registry=registry_provider,
            task_runner=MockTaskRunner(),  # type: ignore[arg-type]
            job_queue=Queue(),
        )
    )
    try:
        for index in range(2):
            run = service.schedule("source_sync", idempotency_key=f"refresh:{index}")
            assert service.claim(run.id)
            await handlers["source_sync"](run.id, JobExecutionContext(service, run.id))  # type: ignore[misc]

        with UnitOfWork(session_factory) as uow:
            assert {event.id for event in uow.activities.list_all()} == {
                connector._event_id for connector in connectors
            }
        assert provider_calls == 2
    finally:
        engine.dispose()


def test_default_worker_composition_registers_all_builtins_without_live_calls(
    tmp_path: Path,
) -> None:
    database = tmp_path / "default-registry.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=f"sqlite:///{database}")
    )

    registry = build_default_source_registry(session_factory)

    assert registry.source_ids == (
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
        "reddit",
    )
    engine.dispose()


@pytest.mark.asyncio
async def test_default_direct_source_reports_missing_auth_without_network(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing-auth.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine, session_factory = create_engine_and_session(
        DatabaseSettings(url=f"sqlite:///{database}")
    )
    registry = build_default_source_registry(session_factory)

    with pytest.raises(MissingSourceConfigurationError, match="bilibili"):
        await registry.get("bilibili").execute(SourceOperation.BOOTSTRAP_IMPORT, limit=1)
    engine.dispose()
