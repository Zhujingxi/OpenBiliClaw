"""Common contracts for boot autostart managers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.config import Config


@dataclass(frozen=True)
class LaunchSpec:
    """Resolved backend launch command for an OS autostart entry."""

    argv: list[str]
    working_dir: Path
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AutostartStatus:
    """Current autostart support and registration state."""

    supported: bool
    registered: bool
    platform: str
    mechanism: str
    reason: str = "none"
    detail: str = ""


class AutostartManager(Protocol):
    """Per-platform user-scope autostart manager."""

    mechanism: str

    def register(self, config: Config) -> None:
        """Register the backend to start on the next user login."""

    def unregister(self) -> None:
        """Remove the user-scope autostart registration."""

    def is_registered(self) -> bool:
        """Return whether the platform entry currently exists."""
