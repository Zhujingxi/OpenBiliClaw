"""Bounded Huey consumer entrypoint for the vNext worker container."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
from collections.abc import Awaitable, Callable, Iterator, Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, cast

from openbiliclaw.features.system.domain import DatabaseSettings, UserSettings
from openbiliclaw.features.system.service import SettingsService
from openbiliclaw.infrastructure.ai.runner import LiteLLMModelResolver, TaskRunner
from openbiliclaw.infrastructure.ai.use_cases import TransactionalAIRunRecorder
from openbiliclaw.infrastructure.database.base import create_engine_and_session
from openbiliclaw.infrastructure.database.operations import require_schema_at_head
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs.orchestration import (
    WorkerDependencies,
    build_worker_runtime,
)
from openbiliclaw.infrastructure.jobs.queue import huey
from openbiliclaw.infrastructure.jobs.source_composition import (
    MissingSourceConfigurationError,
    build_default_source_registry,
)
from openbiliclaw.infrastructure.jobs.tasks import (
    JobHandler,
    JobService,
    configure_job_runtime,
)
from openbiliclaw.infrastructure.runtime_settings import applied_runtime_settings
from openbiliclaw.logging_setup import (
    DeploymentLoggingSettings,
    installed_owned_logging_handlers,
)

MAX_WORKERS = 4
ASYNC_JOB_TIMEOUT_SECONDS = 3600
ASYNC_SHUTDOWN_TIMEOUT_SECONDS = 30
SettingsLoader = Callable[[], UserSettings]


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    """Composed job runtime plus async transport cleanup owned by the worker."""

    service: JobService
    handlers: Mapping[str, JobHandler]
    async_shutdown: Callable[[], Awaitable[None]] | None = None

    def __iter__(self) -> Iterator[object]:
        """Keep the service/handler pair convenient for composition inspection."""

        yield self.service
        yield self.handlers


RuntimeFactory = Callable[[], WorkerRuntime | tuple[JobService, Mapping[str, JobHandler]]]


class AsyncJobExecutor:
    """Run every Huey coroutine on one worker-lifecycle event loop."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = Event()
        self._state_lock = Lock()
        self._accepting = True
        self._closed = False
        self._futures: set[Future[None]] = set()
        self._active: set[Event] = set()
        self._thread = Thread(
            target=self._serve,
            name="openbiliclaw-async-jobs",
            daemon=False,
        )
        self._thread.start()
        if not self._ready.wait(timeout=ASYNC_SHUTDOWN_TIMEOUT_SECONDS):
            raise RuntimeError("worker async event loop did not start")

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._bounded_loop_cleanup()
            self._loop.close()

    def _bounded_loop_cleanup(self) -> None:
        """Cancel loop-owned work without trusting third-party cancellation."""

        deadline = self._loop.time() + ASYNC_SHUTDOWN_TIMEOUT_SECONDS
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            _, pending = self._loop.run_until_complete(
                asyncio.wait(
                    pending,
                    timeout=max(0.0, deadline - self._loop.time()),
                )
            )
        self._abandon_tasks(pending)
        remaining = max(0.0, deadline - self._loop.time())
        if remaining == 0:
            return
        asyncgen_shutdown = self._loop.create_task(self._loop.shutdown_asyncgens())
        _, unfinished = self._loop.run_until_complete(
            asyncio.wait({asyncgen_shutdown}, timeout=remaining)
        )
        self._abandon_tasks(unfinished)

    @staticmethod
    def _abandon_tasks(tasks: set[asyncio.Task[Any]]) -> None:
        """Detach cancellation-resistant tasks after the hard shutdown bound."""

        for task in tasks:
            task.cancel()
            # The loop is intentionally closing: logging an unavoidable
            # "destroyed pending task" warning would imply it can run again.
            cast("Any", task)._log_destroy_pending = False

    def _submission_finished(self, completion: Event) -> None:
        completion.set()
        with self._state_lock:
            self._active.discard(completion)

    def _future_finished(self, future: Future[None]) -> None:
        with self._state_lock:
            self._futures.discard(future)

    def run(self, result: Awaitable[None]) -> None:
        """Submit an async handler from any Huey thread and propagate its outcome."""

        completion = Event()
        with self._state_lock:
            if not self._accepting:
                if inspect.iscoroutine(result):
                    result.close()
                raise RuntimeError("worker async event loop is closed")
            self._active.add(completion)
            future = asyncio.run_coroutine_threadsafe(
                _await_result(
                    result,
                    on_finished=lambda: self._submission_finished(completion),
                ),
                self._loop,
            )
            self._futures.add(future)
        future.add_done_callback(self._future_finished)
        try:
            future.result(timeout=ASYNC_JOB_TIMEOUT_SECONDS)
        except FutureTimeoutError as exc:
            cancelled = future.cancel()
            if not completion.wait(timeout=ASYNC_SHUTDOWN_TIMEOUT_SECONDS):
                with self._state_lock:
                    self._accepting = False
                raise RuntimeError(
                    "worker async job did not stop after cancellation; restart required"
                ) from exc
            if not cancelled:
                future.result()
            raise TimeoutError("worker async job exceeded the lifecycle bound") from exc

    def close(self, shutdown: Callable[[], Awaitable[None]] | None = None) -> None:
        """Close loop-bound transports, stop the loop, and join its thread."""

        with self._state_lock:
            if self._closed:
                return
            self._accepting = False
            self._closed = True
            active = tuple(self._active)
            futures = tuple(self._futures)
        shutdown_error: BaseException | None = None
        try:
            for future in futures:
                future.cancel()
            cancellation_deadline = monotonic() + ASYNC_SHUTDOWN_TIMEOUT_SECONDS
            for completion in active:
                completion.wait(timeout=max(0.0, cancellation_deadline - monotonic()))
            if shutdown is not None:
                future = asyncio.run_coroutine_threadsafe(
                    _await_result(shutdown()),
                    self._loop,
                )
                try:
                    future.result(timeout=ASYNC_SHUTDOWN_TIMEOUT_SECONDS)
                except FutureTimeoutError as exc:
                    future.cancel()
                    shutdown_error = TimeoutError("worker async transport shutdown timed out")
                    shutdown_error.__cause__ = exc
                except BaseException as exc:
                    shutdown_error = exc
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=ASYNC_SHUTDOWN_TIMEOUT_SECONDS * 2)
        if self._thread.is_alive():
            raise RuntimeError("worker async event loop did not stop")
        if shutdown_error is not None:
            raise shutdown_error


async def _await_result(
    result: Awaitable[None],
    *,
    on_finished: Callable[[], None] | None = None,
) -> None:
    try:
        await result
    finally:
        if on_finished is not None:
            on_finished()


def _load_factory(path: str) -> RuntimeFactory:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("worker runtime factory must use module:function syntax")
    value = getattr(importlib.import_module(module_name), attribute)
    if not callable(value):
        raise TypeError("worker runtime factory is not callable")
    return cast("RuntimeFactory", value)


def database_runtime_factory() -> WorkerRuntime:
    """Compose real handlers with all seven explicit built-in source connectors."""

    settings = DatabaseSettings()
    require_schema_at_head(
        database_url=settings.url,
        alembic_ini=Path(os.getenv("OPENBILICLAW_ALEMBIC_INI", "alembic.ini")),
    )
    _engine, session_factory = create_engine_and_session(settings)
    base_url = os.getenv("OPENBILICLAW_LITELLM_BASE_URL", "http://litellm:4000")
    api_key = os.getenv("OPENBILICLAW_LITELLM_API_KEY")
    if not api_key:
        raise RuntimeError("OPENBILICLAW_LITELLM_API_KEY is required for the worker")
    product_settings = SettingsService(
        cast("Callable[[], Any]", lambda: UnitOfWork(session_factory))
    )
    model_resolver = LiteLLMModelResolver(base_url=base_url, api_key=api_key)
    runner = TaskRunner(
        model_resolver=model_resolver,
        recorder=TransactionalAIRunRecorder(lambda: UnitOfWork(session_factory)),
        settings=product_settings,
    )
    service, handlers = build_worker_runtime(
        WorkerDependencies(
            session_factory=session_factory,
            # Resolve persisted per-source transport settings for each job.
            # The worker is long-lived and must not retain its startup mode.
            source_registry=lambda: build_default_source_registry(session_factory),
            task_runner=runner,
        )
    )
    return WorkerRuntime(
        service=service,
        handlers=handlers,
        async_shutdown=model_resolver.aclose,
    )


def _load_database_user_settings() -> UserSettings:
    database = DatabaseSettings()
    require_schema_at_head(
        database_url=database.url,
        alembic_ini=Path(os.getenv("OPENBILICLAW_ALEMBIC_INI", "alembic.ini")),
    )
    engine, session_factory = create_engine_and_session(database)
    try:
        service = SettingsService(cast("Callable[[], Any]", lambda: UnitOfWork(session_factory)))
        return service.get()
    finally:
        engine.dispose()


def run_worker(
    runtime_factory: RuntimeFactory,
    *,
    workers: int = MAX_WORKERS,
    settings_loader: SettingsLoader | None = None,
    deployment_logging: DeploymentLoggingSettings | None = None,
) -> None:
    """Configure dependencies, recover interrupted runs, and start the consumer."""

    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"worker concurrency must be between 1 and {MAX_WORKERS}")
    loader = settings_loader or _load_database_user_settings
    logging_config = deployment_logging or DeploymentLoggingSettings()
    with installed_owned_logging_handlers(logging_config), applied_runtime_settings(loader()):
        composed = runtime_factory()
        runtime = (
            composed
            if isinstance(composed, WorkerRuntime)
            else WorkerRuntime(service=composed[0], handlers=composed[1])
        )
        async_executor = AsyncJobExecutor()
        configure_job_runtime(
            runtime.service,
            runtime.handlers,
            async_job_runner=async_executor.run,
        )
        consumer: Any | None = None
        try:
            runtime.service.recover_interrupted()
            consumer = huey.create_consumer(
                workers=workers,
                worker_type="thread",
                periodic=True,
                flush_locks=True,
            )
            # ``Consumer.start()`` only launches daemon threads and returns. The
            # supported worker process must own Huey's blocking supervision loop so
            # PID 1 remains alive, performs health checks, and handles signals.
            consumer.run()
        finally:
            try:
                if consumer is not None:
                    # SIGTERM is non-graceful inside Huey: ``run()`` returns
                    # before thread workers finish. Join them before removing
                    # the shared runner so a durable RUNNING row either
                    # succeeds or remains recoverable after a hard process kill.
                    consumer.stop(graceful=True)
            finally:
                configure_job_runtime(runtime.service, runtime.handlers)
                async_executor.close(runtime.async_shutdown)


def main() -> None:
    """Run the consumer using the application composition factory from the environment."""

    factory_path = os.getenv(
        "OPENBILICLAW_WORKER_RUNTIME_FACTORY",
        "openbiliclaw.infrastructure.jobs.worker:database_runtime_factory",
    )
    workers = int(os.getenv("OPENBILICLAW_WORKERS", str(MAX_WORKERS)))
    run_worker(_load_factory(factory_path), workers=workers)


if __name__ == "__main__":
    main()


__all__ = [
    "MAX_WORKERS",
    "AsyncJobExecutor",
    "MissingSourceConfigurationError",
    "WorkerRuntime",
    "build_default_source_registry",
    "database_runtime_factory",
    "main",
    "run_worker",
]
