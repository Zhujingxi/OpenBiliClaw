"""Real business handlers composed from vNext use cases and injected adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from openbiliclaw.features.activity.domain import ActivityEvent
from openbiliclaw.features.activity.service import ActivityService, project_activity_event
from openbiliclaw.features.feed.service import FeedService
from openbiliclaw.features.profile.service import ProfileService
from openbiliclaw.features.sources.domain import SourceOperation, SourceResultKind
from openbiliclaw.features.system.service import SettingsService
from openbiliclaw.infrastructure.ai.use_cases import (
    TaskRunnerBatchAssessor,
    TaskRunnerProfileDeltaAI,
)
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs.tasks import (
    JobHandler,
    JobService,
    PermanentJobError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from uuid import UUID

    from sqlalchemy.orm import Session, sessionmaker

    from openbiliclaw.features.activity.domain import ProfileSignal
    from openbiliclaw.features.sources.registry import SourceRegistry
    from openbiliclaw.infrastructure.ai.runner import TaskRunner


@dataclass(frozen=True, slots=True)
class WorkerDependencies:
    """Explicit composition inputs; source and model transports remain injectable."""

    session_factory: sessionmaker[Session]
    source_registry: SourceRegistry
    task_runner: TaskRunner


class WorkerOrchestrator:
    """Execute the four named jobs through application services, never model workflow control."""

    def __init__(self, dependencies: WorkerDependencies, job_service: JobService) -> None:
        self._dependencies = dependencies
        self._uow_factory: Callable[[], UnitOfWork] = lambda: UnitOfWork(
            dependencies.session_factory
        )
        self._settings = SettingsService(cast("Callable[[], Any]", self._uow_factory))
        self._activity = ActivityService(cast("Callable[[], Any]", self._uow_factory))
        self._profile = ProfileService(
            cast("Callable[[], Any]", self._uow_factory),
            ai=TaskRunnerProfileDeltaAI(dependencies.task_runner),
        )
        self._feed = FeedService(
            cast("Callable[[], Any]", self._uow_factory),
            connectors=dependencies.source_registry.connectors,
            assessor=TaskRunnerBatchAssessor(dependencies.task_runner),
            settings=self._settings,
        )
        self._jobs = job_service

    async def source_sync(self, _run_id: UUID) -> None:
        """Import deterministic activity from every enabled bootstrap connector."""

        settings = self._settings.get()
        for source_id in sorted(settings.source_enabled):
            if not settings.source_enabled[source_id]:
                continue
            try:
                connector = self._dependencies.source_registry.get(source_id)
            except LookupError as exc:
                raise PermanentJobError(
                    f"enabled source has no configured connector: {source_id}"
                ) from exc
            bootstrap = next(
                (
                    spec
                    for spec in connector.manifest.operations
                    if spec.operation is SourceOperation.BOOTSTRAP_IMPORT
                    and spec.result_kind is SourceResultKind.ACTIVITY
                ),
                None,
            )
            if bootstrap is None:
                raise PermanentJobError(
                    f"enabled source does not support activity sync: {source_id}"
                )
            result = await connector.execute(SourceOperation.BOOTSTRAP_IMPORT, limit=100)
            if not all(isinstance(event, ActivityEvent) for event in result):
                raise TypeError(f"source sync returned non-activity data: {source_id}")
            for event in cast("tuple[ActivityEvent, ...]", result):
                self._activity.ingest(event)

    async def profile_projection(self, _run_id: UUID) -> None:
        """Project only activity evidence absent from the latest immutable revision."""

        with self._uow_factory() as uow:
            events = uow.activities.list_all()
            current = uow.profiles.latest()
        projected = {
            evidence_id
            for facet in (() if current is None else current.facets)
            for evidence_id in facet.evidence_ids
        }
        signals: list[ProfileSignal] = []
        for event in events:
            if event.id not in projected:
                signals.extend(project_activity_event(event))
        if signals:
            await self._profile.project(tuple(signals))

    async def feed_replenishment(self, _run_id: UUID) -> None:
        """Run deterministic collection and bounded typed batch admission."""

        await self._feed.replenish()

    def cleanup(self, _run_id: UUID) -> None:
        """Delete only terminal business-job history older than retention."""

        self._jobs.cleanup_finished(retention_days=30)

    def handlers(self) -> Mapping[str, JobHandler]:
        return {
            "source_sync": self.source_sync,
            "profile_projection": self.profile_projection,
            "feed_replenishment": self.feed_replenishment,
            "cleanup": self.cleanup,
        }


def build_worker_runtime(
    dependencies: WorkerDependencies,
) -> tuple[JobService, Mapping[str, JobHandler]]:
    """Build the real four-handler runtime over one application database."""

    from openbiliclaw.infrastructure.jobs.tasks import HueyJobQueue

    def uow_factory() -> UnitOfWork:
        return UnitOfWork(dependencies.session_factory)

    service = JobService(cast("Callable[[], Any]", uow_factory), queue=HueyJobQueue())
    orchestrator = WorkerOrchestrator(dependencies, service)
    return service, orchestrator.handlers()


__all__ = ["WorkerDependencies", "WorkerOrchestrator", "build_worker_runtime"]
