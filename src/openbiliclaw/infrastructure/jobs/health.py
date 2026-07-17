"""Rollback-only write diagnostic for the durable vNext worker container."""

from __future__ import annotations

import os
from pathlib import Path

from openbiliclaw.features.system.domain import DatabaseSettings
from openbiliclaw.infrastructure.database.operations import SQLiteOperationalStore

PID_ONE_CMDLINE = Path("/proc/1/cmdline")
WORKER_MODULE = b"openbiliclaw.worker"


def _is_worker_process(cmdline: bytes) -> bool:
    parts = tuple(part for part in cmdline.split(b"\0") if part)
    if len(parts) < 3:
        return False
    executable = Path(os.fsdecode(parts[0])).name
    if not executable.startswith("python"):
        return False
    if parts[1:] == (b"-m", WORKER_MODULE):
        return True
    if Path(os.fsdecode(parts[1])).name != "openbiliclaw" or parts[2] != b"worker":
        return False
    if len(parts) == 3:
        return True
    return (
        len(parts) == 5
        and parts[3] == b"--workers"
        and parts[4]
        in {
            b"1",
            b"2",
            b"3",
            b"4",
        }
    )


def worker_health_ready(*, process_cmdline: bytes | None = None) -> bool:
    """Require the exact worker process, current schema, and writable queue."""

    try:
        cmdline = PID_ONE_CMDLINE.read_bytes() if process_cmdline is None else process_cmdline
        if not _is_worker_process(cmdline):
            return False
        persistence = SQLiteOperationalStore().diagnose(
            database_url=DatabaseSettings().url,
            queue_path=Path(os.getenv("OPENBILICLAW_HUEY_PATH", "data/vnext/huey.db")),
            alembic_ini=Path(os.getenv("OPENBILICLAW_ALEMBIC_INI", "alembic.ini")),
        )
    except Exception:  # noqa: BLE001 - container probes fail closed
        return False
    return all(
        (
            persistence.database_reachable,
            persistence.database_integrity_ok,
            persistence.migration_at_head,
            persistence.queue_exists,
            persistence.queue_integrity_ok,
            persistence.queue_writable,
            persistence.paths_separate,
        )
    )


def main() -> None:
    """Exit successfully only when every worker runtime dependency is usable."""

    raise SystemExit(0 if worker_health_ready() else 1)


if __name__ == "__main__":
    main()


__all__ = ["main", "worker_health_ready"]
