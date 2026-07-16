"""Operational-only command line interface for the vNext runtime."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Annotated, cast

import typer
import uvicorn
from alembic import command
from alembic.config import Config
from pydantic_evals import Dataset
from sqlalchemy.engine import make_url

from openbiliclaw.features.system.domain import DatabaseSettings
from openbiliclaw.infrastructure.ai.evaluators import TASK_EVALUATOR_TYPES

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
    """Validate versioned eval datasets without invoking a model provider."""

    root = Path("evals/datasets")
    names = (
        (dataset,)
        if dataset
        else (
            "profile_delta",
            "keyword_generation",
            "candidate_assessment",
            "recommendation_explanation",
        )
    )
    for name in names:
        path = root / f"{name}.yaml"
        Dataset[dict[str, object], dict[str, object], dict[str, object]].from_file(
            path,
            custom_evaluator_types=TASK_EVALUATOR_TYPES,
        )
        typer.echo(f"valid: {name}")
    return 0


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
    """Run secret-safe local configuration and database diagnostics."""

    settings = DatabaseSettings()
    parsed = make_url(settings.url)
    access_configured = bool(os.getenv("OPENBILICLAW_ACCESS_TOKEN"))
    litellm_configured = bool(os.getenv("OPENBILICLAW_LITELLM_API_KEY"))
    database_ready = _database_reachable(parsed.database, parsed.get_backend_name())
    typer.echo(f"database: {'ready' if database_ready else 'not-ready'}")
    typer.echo(f"access-control: {'configured' if access_configured else 'missing'}")
    typer.echo(f"litellm: {'configured' if litellm_configured else 'missing'}")
    if not all((database_ready, access_configured, litellm_configured)):
        raise typer.Exit(code=1)


def _database_reachable(database: str | None, backend: str) -> bool:
    if backend != "sqlite" or not database or database == ":memory:":
        return backend == "sqlite" and database == ":memory:"
    path = Path(database).expanduser()
    if not path.exists():
        return False
    try:
        with sqlite3.connect(path) as connection:
            result = cast(
                "tuple[str] | None",
                connection.execute("pragma integrity_check").fetchone(),
            )
            return result == ("ok",)
    except sqlite3.Error:
        return False


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
    source = Path(parsed.database).expanduser().resolve()
    target = destination.expanduser().resolve()
    if source == target:
        raise typer.BadParameter("backup destination must differ from the database")
    if not source.is_file():
        raise typer.BadParameter("configured vNext database does not exist")
    if target.exists():
        raise typer.BadParameter("backup destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
            source_db.backup(target_db)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    typer.echo(f"backup created: {target}")


def main() -> None:
    """Execute the operational CLI for ``python -m openbiliclaw.cli``."""

    app()


if __name__ == "__main__":
    main()


__all__ = ["app", "main", "run_offline_evals", "run_server", "run_worker_process"]
