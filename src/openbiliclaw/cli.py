"""Operational-only command line interface for the vNext runtime."""

from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from alembic import command
from alembic.config import Config
from pydantic_evals import Dataset
from pydantic_evals.evaluators import LLMJudge
from sqlalchemy.engine import make_url

from openbiliclaw.features.system.domain import DatabaseSettings
from openbiliclaw.infrastructure.ai.evaluators import TASK_EVALUATOR_TYPES
from openbiliclaw.infrastructure.ai.health import (
    ALIASES,
    AIHealthResult,
    AIHealthService,
    AliasHealth,
)
from openbiliclaw.infrastructure.database.operations import (
    DatabaseBackupError,
    SQLiteOperationalStore,
)

EVAL_DATASET_ROOT = Path("evals/datasets")
EVAL_DATASET_NAMES = (
    "profile_delta",
    "keyword_generation",
    "candidate_assessment",
    "recommendation_explanation",
)

app = typer.Typer(no_args_is_help=True, add_completion=False, help="OpenBiliClaw operations")
db_app = typer.Typer(no_args_is_help=True, add_completion=False, help="vNext database operations")
app.add_typer(db_app, name="db")


def run_server(*, host: str, port: int, reload: bool) -> None:
    """Start the authoritative FastAPI application."""

    uvicorn.run("openbiliclaw.api.app:app", host=host, port=port, reload=reload)


def run_worker_process(workers: int) -> None:
    """Start the supported bounded Huey worker."""

    from openbiliclaw.worker import run

    run(workers=workers)


def run_offline_evals(dataset: str | None) -> int:
    """Execute versioned dataset cases and deterministic offline evaluators."""

    if dataset and dataset not in EVAL_DATASET_NAMES:
        typer.echo(f"failed: {dataset} cases=0 assertions=0")
        return 1
    names = (dataset,) if dataset else EVAL_DATASET_NAMES
    failed = False
    for name in names:
        try:
            cases, assertions, passed = _evaluate_dataset(EVAL_DATASET_ROOT / f"{name}.yaml")
        except Exception:  # noqa: BLE001 - eval reports failures without leaking internals
            cases, assertions, passed = 0, 0, False
        status = "passed" if passed else "failed"
        typer.echo(f"{status}: {name} cases={cases} assertions={assertions}")
        failed |= not passed
    return int(failed)


def _evaluate_dataset(path: Path) -> tuple[int, int, bool]:
    return asyncio.run(_evaluate_dataset_async(path))


async def _evaluate_dataset_async(path: Path) -> tuple[int, int, bool]:
    dataset = Dataset[dict[str, object], dict[str, object], dict[str, object]].from_file(
        path,
        custom_evaluator_types=TASK_EVALUATOR_TYPES,
    )
    evaluators = tuple(
        evaluator for evaluator in dataset.evaluators if not isinstance(evaluator, LLMJudge)
    )
    if not dataset.cases or not evaluators:
        return len(dataset.cases), 0, False

    assertion_count = 0
    passed = True
    for case in dataset.cases:
        if case.expected_output is None:
            passed = False
            continue
        report = await Dataset(
            name=f"{dataset.name}_offline",
            cases=[case],
            evaluators=list(evaluators),
        ).evaluate(lambda _inputs, output=case.expected_output: deepcopy(output), progress=False)
        report_case = report.cases[0]
        assertion_count += len(report_case.assertions)
        passed &= not report_case.evaluator_failures and all(
            result.value is True for result in report_case.assertions.values()
        )
    return len(dataset.cases), assertion_count, passed


def run_ai_health_check(*, base_url: str, api_key: str) -> AIHealthResult:
    """Run the explicit LiteLLM health diagnostic at a synchronous CLI boundary."""

    async def check() -> AIHealthResult:
        service = AIHealthService(base_url=base_url, api_key=api_key)
        try:
            return await service.check_aliases()
        finally:
            await service.aclose()

    return asyncio.run(check())


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option(help="Bind address")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8420,
    reload: Annotated[bool, typer.Option(help="Reload on source changes")] = False,
) -> None:
    """Run the vNext API and existing static web."""

    run_server(host=host, port=port, reload=reload)


@app.command("worker")
def worker(
    workers: Annotated[int, typer.Option(min=1, max=4)] = 4,
) -> None:
    """Run the bounded Huey worker."""

    run_worker_process(workers)


@app.command("doctor")
def doctor() -> None:
    """Run secret-safe persistence, migration, access, and AI diagnostics."""

    settings = DatabaseSettings()
    access_configured = bool(os.getenv("OPENBILICLAW_ACCESS_TOKEN"))
    queue_path = Path(os.getenv("OPENBILICLAW_HUEY_PATH", "data/vnext/huey.db"))
    alembic_ini = Path(os.getenv("OPENBILICLAW_ALEMBIC_INI", "alembic.ini"))
    try:
        persistence = SQLiteOperationalStore().diagnose(
            database_url=settings.url,
            queue_path=queue_path,
            alembic_ini=alembic_ini,
        )
    except Exception:  # noqa: BLE001 - diagnostics fail closed without leaking details
        persistence = None

    database_ready = bool(
        persistence and persistence.database_reachable and persistence.database_integrity_ok
    )
    migration_ready = bool(persistence and persistence.migration_at_head)
    queue_ready = bool(
        persistence
        and persistence.queue_exists
        and persistence.queue_integrity_ok
        and persistence.queue_writable
    )
    separation_ready = bool(persistence and persistence.paths_separate)
    typer.echo(f"database: {'ready' if database_ready else 'not-ready'}")
    typer.echo(f"migration: {'head' if migration_ready else 'stale'}")
    typer.echo(f"queue: {'ready' if queue_ready else 'not-ready'}")
    typer.echo(f"queue-separation: {'ready' if separation_ready else 'invalid'}")
    typer.echo(f"access-control: {'configured' if access_configured else 'missing'}")
    api_key = os.getenv("OPENBILICLAW_LITELLM_API_KEY")
    health = _unavailable_aliases("proxy_not_configured")
    if api_key:
        try:
            health = run_ai_health_check(
                base_url=os.getenv("OPENBILICLAW_LITELLM_BASE_URL", "http://127.0.0.1:4000"),
                api_key=api_key,
            )
        except Exception:  # noqa: BLE001 - a diagnostic must fail closed and redact details
            health = _unavailable_aliases("diagnostic_error")
    typer.echo(f"litellm: {'reachable' if health.proxy_reachable else 'unreachable'}")
    statuses = {status.alias: status for status in health.aliases}
    aliases_exact = len(health.aliases) == len(ALIASES) and set(statuses) == set(ALIASES)
    aliases_ready = aliases_exact
    for alias in ALIASES:
        status = statuses.get(alias)
        state = status.state if status is not None else "unavailable"
        typer.echo(f"{alias}: {state}")
        aliases_ready &= status is not None and status.available and status.state == "healthy"
    if not all(
        (
            database_ready,
            migration_ready,
            queue_ready,
            separation_ready,
            access_configured,
            health.proxy_reachable,
            aliases_ready,
        )
    ):
        raise typer.Exit(code=1)


def _unavailable_aliases(reason: str) -> AIHealthResult:
    return AIHealthResult(
        proxy_reachable=False,
        aliases=tuple(
            AliasHealth(alias=alias, available=False, state="unavailable", reason=reason)
            for alias in ALIASES
        ),
    )


@app.command("eval")
def eval_command(
    dataset: Annotated[str | None, typer.Option(help="Validate one dataset")] = None,
) -> None:
    """Validate offline eval datasets; no live model call is made."""

    raise typer.Exit(code=run_offline_evals(dataset))


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply Alembic migrations to the configured fresh vNext database."""

    settings = DatabaseSettings()
    config = Config(os.getenv("OPENBILICLAW_ALEMBIC_INI", "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.url)
    command.upgrade(config, "head")
    typer.echo("vNext database migrated")


@db_app.command("backup")
def db_backup(
    destination: Annotated[Path, typer.Argument(help="New SQLite backup path")],
) -> None:
    """Create a consistent backup of the configured vNext SQLite database."""

    parsed = make_url(DatabaseSettings().url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database:
        raise typer.BadParameter("database backup requires file-backed SQLite")
    try:
        target = SQLiteOperationalStore().backup(
            source=Path(parsed.database), destination=destination
        )
    except DatabaseBackupError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"backup created: {target}")


def main() -> None:
    """Execute the operational CLI for ``python -m openbiliclaw.cli``."""

    app()


if __name__ == "__main__":
    main()


__all__ = [
    "app",
    "main",
    "run_ai_health_check",
    "run_offline_evals",
    "run_server",
    "run_worker_process",
]
