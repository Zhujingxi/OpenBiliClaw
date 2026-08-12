"""Frozen desktop entrypoint over the single composition process graph."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _data_dir() -> Path:
    override = os.environ.get("OPENBILICLAW_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenBiliClaw"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "OpenBiliClaw"
    return Path.home() / ".local" / "share" / "openbiliclaw"


def main() -> None:
    """Run a leak-checked graph self-check or the local API host."""
    from openbiliclaw.composition.entrypoints import main as composition_main

    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    os.environ.setdefault("OPENBILICLAW_FRONTEND_DIR", str(bundled / "openbiliclaw/frontend"))
    command = "check" if os.environ.get("OPENBILICLAW_SELFTEST") else "serve"
    sys.argv = ["openbiliclaw", command, "--data-dir", str(_data_dir())]
    composition_main()


if __name__ == "__main__":
    main()
