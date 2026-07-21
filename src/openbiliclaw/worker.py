"""Supported module entrypoint for the bounded vNext Huey worker."""

from __future__ import annotations

from openbiliclaw.infrastructure.jobs.worker import (
    MAX_WORKERS,
    database_runtime_factory,
    run_worker,
)
from openbiliclaw.infrastructure.jobs.worker import (
    main as run_configured_worker,
)


def run(*, workers: int = MAX_WORKERS) -> None:
    run_worker(database_runtime_factory, workers=workers)


def main() -> None:
    run_configured_worker()


if __name__ == "__main__":
    main()


__all__ = ["main", "run"]
