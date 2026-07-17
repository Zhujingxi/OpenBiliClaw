"""Small, deployment-owned logging boundary for API and worker processes."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from rich.logging import RichHandler

if TYPE_CHECKING:
    from collections.abc import Iterator

_OWNED_SINK_ATTRIBUTE = "_openbiliclaw_sink"


@dataclass(frozen=True, slots=True)
class DeploymentLoggingSettings:
    """Hidden infrastructure defaults for process-owned log sinks.

    Mutable thresholds are applied from :class:`UserSettings`; file location
    and retention are deployment details and deliberately stay outside the UI.
    """

    level: str = "INFO"
    file_level: str = "DEBUG"
    directory: str = "logs"
    filename: str = "openbiliclaw.log"
    max_file_size_mb: int = 100
    backup_count: int = 1

    @property
    def file_path(self) -> Path:
        return Path(self.directory) / self.filename


def _coerce_level(level_name: str) -> int:
    level = logging.getLevelName(level_name.upper())
    return level if isinstance(level, int) else logging.INFO


def apply_owned_handler_levels(*, console_level: str, file_level: str) -> None:
    """Update only handlers installed by this module."""

    levels = {"console": _coerce_level(console_level), "file": _coerce_level(file_level)}
    for handler in logging.getLogger().handlers:
        sink = getattr(handler, _OWNED_SINK_ATTRIBUTE, None)
        if sink in levels:
            handler.setLevel(levels[sink])


def snapshot_owned_handler_levels() -> tuple[tuple[logging.Handler, int], ...]:
    """Capture process-owned handler levels without touching host handlers."""

    return tuple(
        (handler, handler.level)
        for handler in logging.getLogger().handlers
        if getattr(handler, _OWNED_SINK_ATTRIBUTE, None) in {"console", "file"}
    )


def restore_owned_handler_levels(
    snapshot: tuple[tuple[logging.Handler, int], ...],
) -> None:
    """Restore handler levels captured by :func:`snapshot_owned_handler_levels`."""

    for handler, level in snapshot:
        handler.setLevel(level)


@contextmanager
def installed_owned_logging_handlers(
    config: DeploymentLoggingSettings,
) -> Iterator[None]:
    """Install missing console/file sinks for one bounded process lifecycle."""

    root_logger = logging.getLogger()
    package_logger = logging.getLogger("openbiliclaw")
    package_level = package_logger.level
    package_disabled = package_logger.disabled
    created: list[logging.Handler] = []
    try:
        package_logger.setLevel(logging.DEBUG)
        package_logger.disabled = False
        existing = {
            getattr(handler, _OWNED_SINK_ATTRIBUTE, None) for handler in root_logger.handlers
        }
        if "console" not in existing:
            console = RichHandler(rich_tracebacks=True, show_path=False)
            setattr(console, _OWNED_SINK_ATTRIBUTE, "console")
            console.setLevel(_coerce_level(config.level))
            console.setFormatter(logging.Formatter("%(message)s"))
            root_logger.addHandler(console)
            created.append(console)
        if "file" not in existing:
            config.file_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                config.file_path,
                maxBytes=max(config.max_file_size_mb, 0) * 1024 * 1024,
                backupCount=max(config.backup_count, 0),
                encoding="utf-8",
            )
            setattr(file_handler, _OWNED_SINK_ATTRIBUTE, "file")
            file_handler.setLevel(_coerce_level(config.file_level))
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            root_logger.addHandler(file_handler)
            created.append(file_handler)
        yield
    finally:
        for handler in reversed(created):
            root_logger.removeHandler(handler)
            handler.close()
        package_logger.setLevel(package_level)
        package_logger.disabled = package_disabled


__all__ = [
    "DeploymentLoggingSettings",
    "apply_owned_handler_levels",
    "installed_owned_logging_handlers",
    "restore_owned_handler_levels",
    "snapshot_owned_handler_levels",
]
