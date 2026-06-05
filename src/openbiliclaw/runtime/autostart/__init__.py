"""User-scope boot autostart registration."""

from __future__ import annotations

import sys

from openbiliclaw import docker_runtime

from .base import AutostartManager, AutostartStatus


def _unsupported_reason() -> str:
    if docker_runtime.is_running_in_container():
        return "unsupported_docker_runtime"
    return "unsupported_platform"


def get_manager() -> AutostartManager | None:
    """Return the current platform manager, or ``None`` when unsupported."""
    if docker_runtime.is_running_in_container():
        return None

    try:
        if sys.platform == "darwin":
            from .macos import MacOSLaunchAgentManager

            return MacOSLaunchAgentManager()
        if sys.platform == "win32":
            from .windows import WindowsRunManager

            return WindowsRunManager()
        if sys.platform.startswith("linux"):
            from .linux import LinuxXdgAutostartManager

            return LinuxXdgAutostartManager()
    except ModuleNotFoundError:
        return None
    return None


def is_supported() -> bool:
    """Return whether the current runtime can manage user autostart."""
    return get_manager() is not None


def register(config: object) -> None:
    """Register the current platform autostart entry."""
    manager = get_manager()
    if manager is None:
        raise RuntimeError(_unsupported_reason())
    manager.register(config)  # type: ignore[arg-type]


def unregister() -> None:
    """Remove the current platform autostart entry."""
    manager = get_manager()
    if manager is None:
        raise RuntimeError(_unsupported_reason())
    manager.unregister()


def status() -> AutostartStatus:
    """Return current platform autostart status."""
    manager = get_manager()
    if manager is None:
        return AutostartStatus(
            supported=False,
            registered=False,
            platform=sys.platform,
            mechanism="none",
            reason=_unsupported_reason(),
        )
    return AutostartStatus(
        supported=True,
        registered=manager.is_registered(),
        platform=sys.platform,
        mechanism=manager.mechanism,
        reason="none",
    )


__all__ = [
    "AutostartManager",
    "AutostartStatus",
    "get_manager",
    "is_supported",
    "register",
    "status",
    "unregister",
]
