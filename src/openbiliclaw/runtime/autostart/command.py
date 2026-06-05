"""Resolve the backend launch command used by OS autostart entries."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from openbiliclaw.config import _project_root

from .base import LaunchSpec

if TYPE_CHECKING:
    from openbiliclaw.config import Config


def _path_with_ollama() -> str:
    path_value = os.environ.get("PATH", "")
    parts = [part for part in path_value.split(os.pathsep) if part]
    ollama = shutil.which("ollama")
    if ollama:
        ollama_dir = str(Path(ollama).resolve().parent)
        if ollama_dir not in parts:
            parts.insert(0, ollama_dir)
    return os.pathsep.join(parts)


def build_launch_spec(config: Config) -> LaunchSpec:
    """Build the stable backend command embedded in OS autostart entries."""
    del config  # The signature stays manager-friendly as config-driven fields grow.
    working_dir = _project_root()
    return LaunchSpec(
        argv=[sys.executable, "-m", "openbiliclaw.cli", "start"],
        working_dir=working_dir,
        env={
            "OPENBILICLAW_PROJECT_ROOT": str(working_dir),
            "PATH": _path_with_ollama(),
        },
    )


def resolve_pythonw(executable: str | Path | None = None) -> Path:
    """Return pythonw.exe next to ``executable`` when present, else the executable."""
    python = Path(executable) if executable is not None else Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else python
