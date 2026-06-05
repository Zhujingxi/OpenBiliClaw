"""Linux XDG desktop autostart manager."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from .command import build_launch_spec

if TYPE_CHECKING:
    from openbiliclaw.config import Config


class LinuxXdgAutostartManager:
    """Manage the user's XDG autostart ``.desktop`` entry."""

    mechanism = "xdg_autostart"

    def __init__(self, *, home: Path | None = None) -> None:
        self._home = home or Path.home()

    @property
    def desktop_path(self) -> Path:
        return self._home / ".config" / "autostart" / "openbiliclaw.desktop"

    def register(self, config: Config) -> None:
        spec = build_launch_spec(config)
        self.desktop_path.parent.mkdir(parents=True, exist_ok=True)
        env_parts = [f"{key}={shlex.quote(value)}" for key, value in spec.env.items()]
        argv = " ".join(shlex.quote(part) for part in spec.argv)
        self.desktop_path.write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=OpenBiliClaw",
                    f"Exec=env {' '.join(env_parts)} {argv}",
                    "X-GNOME-Autostart-enabled=true",
                    "Hidden=false",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def unregister(self) -> None:
        if self.desktop_path.exists():
            self.desktop_path.unlink()

    def is_registered(self) -> bool:
        return self.desktop_path.exists()
