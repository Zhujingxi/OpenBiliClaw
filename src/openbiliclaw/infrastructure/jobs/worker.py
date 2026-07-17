"""Bounded Huey consumer entrypoint for the vNext worker container."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from openbiliclaw.config import LoggingConfig
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
from openbiliclaw.logging_setup import installed_owned_logging_handlers

MAX_WORKERS = 4
RuntimeFactory = Callable[[], tuple[JobService, Mapping[str, JobHandler]]]
SettingsLoader = Callable[[], UserSettings]


def _load_factory(path: str) -> RuntimeFactory:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("worker runtime factory must use module:function syntax")
    value = getattr(importlib.import_module(module_name), attribute)
    if not callable(value):
        raise TypeError("worker runtime factory is not callable")
    return cast("RuntimeFactory", value)


def database_runtime_factory() -> tuple[JobService, Mapping[str, JobHandler]]:
    """Compose real handlers with all seven explicit built-in source connectors."""

    settings = DatabaseSettings()
    require_schema_at_head(
        database_url=settings.url,
        alembic_ini=Path(os.getenv("OPENBILICLAW_ALEMBIC_INI", "alembic.ini")),
    )
    _engine, session_factory = create_engine_and_session(settings)
    source_registry = build_default_source_registry(session_factory)
    base_url = os.getenv("OPENBILICLAW_LITELLM_BASE_URL", "http://litellm:4000")
    api_key = os.getenv("OPENBILICLAW_LITELLM_API_KEY")
    if not api_key:
        raise RuntimeError("OPENBILICLAW_LITELLM_API_KEY is required for the worker")
    product_settings = SettingsService(
        cast("Callable[[], Any]", lambda: UnitOfWork(session_factory))
    )
    runner = TaskRunner(
        model_resolver=LiteLLMModelResolver(base_url=base_url, api_key=api_key),
        recorder=TransactionalAIRunRecorder(lambda: UnitOfWork(session_factory)),
        settings=product_settings,
    )
    return build_worker_runtime(
        WorkerDependencies(
            session_factory=session_factory,
            source_registry=source_registry,
            task_runner=runner,
        )
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
    deployment_logging: LoggingConfig | None = None,
) -> None:
    """Configure dependencies, recover interrupted runs, and start the consumer."""

    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"worker concurrency must be between 1 and {MAX_WORKERS}")
    loader = settings_loader or _load_database_user_settings
    logging_config = deployment_logging or LoggingConfig()
    with installed_owned_logging_handlers(logging_config), applied_runtime_settings(loader()):
        service, handlers = runtime_factory()
        configure_job_runtime(service, handlers)
        service.recover_interrupted()
        consumer = huey.create_consumer(
            workers=workers,
            worker_type="thread",
            periodic=True,
            flush_locks=True,
        )
        consumer.start()


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
    "MissingSourceConfigurationError",
    "build_default_source_registry",
    "database_runtime_factory",
    "main",
    "run_worker",
]
