"""macOS LaunchAgent autostart manager."""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import TYPE_CHECKING

from .command import build_launch_spec

if TYPE_CHECKING:
    from openbiliclaw.config import Config


class MacOSLaunchAgentManager:
    """Manage the user LaunchAgent plist for OpenBiliClaw."""

    mechanism = "launchd"
    label = "com.openbiliclaw.daemon"

    def __init__(self, *, home: Path | None = None) -> None:
        self._home = home or Path.home()

    @property
    def plist_path(self) -> Path:
        return self._home / "Library" / "LaunchAgents" / f"{self.label}.plist"

    def register(self, config: Config) -> None:
        spec = build_launch_spec(config)
        log_dir = config.logging.directory_path
        log_dir.mkdir(parents=True, exist_ok=True)
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": self.label,
            "ProgramArguments": spec.argv,
            "WorkingDirectory": str(spec.working_dir),
            "EnvironmentVariables": spec.env,
            "RunAtLoad": True,
            "KeepAlive": False,
            "StandardOutPath": str(log_dir / "openbiliclaw-autostart.out.log"),
            "StandardErrorPath": str(log_dir / "openbiliclaw-autostart.err.log"),
        }
        self.plist_path.write_bytes(plistlib.dumps(payload, sort_keys=False))

    def unregister(self) -> None:
        if self.plist_path.exists():
            self.plist_path.unlink()

    def is_registered(self) -> bool:
        return self.plist_path.exists()
