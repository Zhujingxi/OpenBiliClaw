"""Isolated SQLite Huey queue configuration."""

from __future__ import annotations

import os
from pathlib import Path

from huey import SqliteHuey

DEFAULT_HUEY_PATH = Path("data/vnext/huey.db")

# Priority values are deliberately sparse so future lanes can be inserted without changing
# persisted job meaning. Higher numeric priority is processed first by SqliteHuey.
PRIORITY_INTERACTIVE = 100
PRIORITY_USER_TRIGGERED = 50
PRIORITY_SCHEDULED = 10


def build_huey(path: str | Path | None = None) -> SqliteHuey:
    """Build the queue in its own file; Huey results are never product state."""

    raw_path: str | Path = (
        path if path is not None else os.getenv("OPENBILICLAW_HUEY_PATH", str(DEFAULT_HUEY_PATH))
    )
    resolved = Path(raw_path)
    resolved.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return SqliteHuey(
        "openbiliclaw-vnext",
        filename=str(resolved),
        results=True,
        utc=True,
        fsync=True,
    )


huey = build_huey()

__all__ = [
    "DEFAULT_HUEY_PATH",
    "PRIORITY_INTERACTIVE",
    "PRIORITY_SCHEDULED",
    "PRIORITY_USER_TRIGGERED",
    "build_huey",
    "huey",
]
