"""Central logging initialization for OpenBiliClaw."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

from rich.logging import RichHandler

if TYPE_CHECKING:
    from collections.abc import Iterator
    from os import stat_result
    from pathlib import Path

    from openbiliclaw.config import Config, LoggingConfig

logger = logging.getLogger(__name__)
_NOISY_LOGGERS = ("httpx", "httpcore", "openai", "openai._base_client")
_OWNED_SINK_ATTRIBUTE = "_openbiliclaw_sink"


def _coerce_level(level_name: str) -> int:
    """Convert a level name to a logging level."""
    level = logging.getLevelName(level_name.upper())
    if isinstance(level, int):
        return level
    return logging.INFO


def apply_owned_handler_levels(*, console_level: str, file_level: str) -> None:
    """Update only handlers installed by :func:`configure_logging`.

    Embedders, pytest's capture handler, and other host-owned root handlers are
    intentionally outside the product-settings boundary.
    """

    levels = {"console": _coerce_level(console_level), "file": _coerce_level(file_level)}
    for handler in logging.getLogger().handlers:
        sink = getattr(handler, _OWNED_SINK_ATTRIBUTE, None)
        if sink in levels:
            handler.setLevel(levels[sink])


def snapshot_owned_handler_levels() -> tuple[tuple[logging.Handler, int], ...]:
    """Capture levels only for handlers installed by :func:`configure_logging`."""

    return tuple(
        (handler, handler.level)
        for handler in logging.getLogger().handlers
        if getattr(handler, _OWNED_SINK_ATTRIBUTE, None) in {"console", "file"}
    )


def restore_owned_handler_levels(
    snapshot: tuple[tuple[logging.Handler, int], ...],
) -> None:
    """Restore a previously captured owned-handler level snapshot."""

    for handler, level in snapshot:
        handler.setLevel(level)


@contextmanager
def installed_owned_logging_handlers(config: LoggingConfig) -> Iterator[None]:
    """Install missing product-owned sinks for a bounded worker lifecycle.

    Unlike :func:`configure_logging`, this preserves every host-installed root
    handler. Existing OpenBiliClaw sinks are reused, and only handlers created
    by this context are removed and closed when the worker exits.
    """

    root_logger = logging.getLogger()
    package_logger = logging.getLogger("openbiliclaw")
    package_level = package_logger.level
    package_disabled = package_logger.disabled
    created: list[logging.Handler] = []

    try:
        package_logger.setLevel(logging.DEBUG)
        package_logger.disabled = False
        existing_sinks = {
            getattr(handler, _OWNED_SINK_ATTRIBUTE, None) for handler in root_logger.handlers
        }

        if "console" not in existing_sinks:
            console_handler = RichHandler(rich_tracebacks=True, show_path=False)
            setattr(console_handler, _OWNED_SINK_ATTRIBUTE, "console")
            console_handler.setLevel(_coerce_level(config.level))
            console_handler.setFormatter(logging.Formatter("%(message)s"))
            root_logger.addHandler(console_handler)
            created.append(console_handler)

        if "file" not in existing_sinks:
            log_file = config.file_path
            log_file.parent.mkdir(parents=True, exist_ok=True)
            _enforce_size_budget_once(log_file, config.max_file_size_mb)
            file_handler = _build_file_handler(
                log_file,
                max_file_size_mb=config.max_file_size_mb,
                backup_count=config.backup_count,
                level=_coerce_level(config.file_level),
            )
            setattr(file_handler, _OWNED_SINK_ATTRIBUTE, "file")
            root_logger.addHandler(file_handler)
            created.append(file_handler)

        yield
    finally:
        for handler in reversed(created):
            root_logger.removeHandler(handler)
            handler.close()
        package_logger.setLevel(package_level)
        package_logger.disabled = package_disabled


def _build_file_handler(
    log_file: object,  # Path, but typed loose to avoid import
    *,
    max_file_size_mb: int,
    backup_count: int,
    level: int,
) -> logging.Handler:
    """Return a rotating file handler when rotation is enabled, else a plain one.

    Rotation triggers when the active file reaches ``max_file_size_mb`` MB; at
    that point ``RotatingFileHandler`` moves it to ``<name>.1`` (older backups
    shift to ``.2``, ``.3``, ...) and older-than-``backup_count`` copies are
    deleted. Setting ``backup_count=1`` caps total disk usage at roughly
    ``2 * max_file_size_mb`` MB.
    """
    from pathlib import Path

    log_path = Path(str(log_file))

    if max_file_size_mb <= 0 or backup_count < 1:
        handler: logging.Handler = logging.FileHandler(log_path, encoding="utf-8")
    else:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=max_file_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding="utf-8",
        )

    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler


def _enforce_size_budget_once(log_file: object, max_file_size_mb: int) -> None:
    """Truncate an oversized log on startup so we don't resume 7 GB files.

    ``RotatingFileHandler`` only rotates on *new* writes, so an already-oversized
    file keeps growing until the next rollover boundary. On startup we proactively
    rotate once if the existing file is already over budget — this is the
    "清理超过 1G 的历史日志" behavior the user asked for.
    """
    from pathlib import Path

    if max_file_size_mb <= 0:
        return

    log_path = Path(str(log_file))
    if not log_path.exists():
        return

    try:
        size = log_path.stat().st_size
    except OSError:
        return

    if size <= max_file_size_mb * 1024 * 1024:
        return

    # Preserve at most one "before cleanup" snapshot so debugging is still
    # possible, then delete further backups. Matches RotatingFileHandler naming
    # (<name>.1 is the freshest backup).
    snapshot = log_path.with_name(log_path.name + ".1")
    try:
        if snapshot.exists():
            snapshot.unlink()
        log_path.rename(snapshot)
    except OSError:
        # Fall back to truncation if rename fails (e.g. cross-device).
        try:
            log_path.unlink()
        except OSError:
            return


def _is_managed_log(path: Path, managed_filename: str) -> bool:
    """True iff ``path`` is the rotation-managed file or one of its backups.

    Managed = ``<filename>`` exactly OR ``<filename>.N`` where N is digits.
    Anything else (e.g. ``backend-restart.log``, ``init-run.log``) is
    unmanaged — created by external scripts or one-off tools, so we treat
    it under the unmanaged-cleanup policy.
    """
    name = path.name
    if name == managed_filename:
        return True
    prefix = managed_filename + "."
    if name.startswith(prefix):
        suffix = name[len(prefix) :]
        return suffix.isdigit()
    return False


def _log_file_entries(log_dir: Path) -> list[tuple[Path, stat_result]] | None:
    try:
        return [(path, path.stat()) for path in log_dir.iterdir() if path.is_file()]
    except OSError:
        return None


def _truncate_unmanaged_logs(
    entries: list[tuple[Path, stat_result]],
    *,
    managed_filename: str,
    threshold_mb: int,
) -> None:
    threshold_bytes = threshold_mb * 1024 * 1024
    for path, stat in entries:
        if _is_managed_log(path, managed_filename):
            continue
        if threshold_mb <= 0 or stat.st_size < threshold_bytes:
            continue
        try:
            size_mb = stat.st_size / (1024 * 1024)
            with path.open("w", encoding="utf-8") as stream:
                stream.write(
                    f"# truncated {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"— was {size_mb:.0f} MB, threshold {threshold_mb} MB\n"
                )
            logger.info("[log-cleanup] truncated %s (was %.0f MB)", path.name, size_mb)
        except OSError as exc:
            logger.debug("Failed to truncate %s: %s", path, exc)


def _delete_stale_unmanaged_logs(
    entries: list[tuple[Path, stat_result]],
    *,
    managed_filename: str,
    age_cutoff: float,
) -> None:
    if not age_cutoff:
        return
    for path, _ in entries:
        if _is_managed_log(path, managed_filename):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime >= age_cutoff:
            continue
        try:
            path.unlink()
            logger.info(
                "[log-cleanup] deleted stale %s (mtime %s)",
                path.name,
                time.strftime("%Y-%m-%d", time.localtime(stat.st_mtime)),
            )
        except OSError as exc:
            logger.debug("Failed to unlink %s: %s", path, exc)


def _enforce_log_budget(log_dir: Path, *, managed_filename: str, aggregate_budget_mb: int) -> None:
    if aggregate_budget_mb <= 0:
        return
    entries = _log_file_entries(log_dir)
    if entries is None:
        return
    budget_bytes = aggregate_budget_mb * 1024 * 1024
    total = sum(stat.st_size for _, stat in entries)
    unmanaged = sorted(
        ((path, stat) for path, stat in entries if not _is_managed_log(path, managed_filename)),
        key=lambda item: item[1].st_mtime,
    )
    for path, stat in unmanaged:
        if total <= budget_bytes:
            break
        try:
            path.unlink()
            total -= stat.st_size
            logger.info(
                "[log-cleanup] deleted %s (%.0f MB) to enforce %d MB budget",
                path.name,
                stat.st_size / (1024 * 1024),
                aggregate_budget_mb,
            )
        except OSError as exc:
            logger.debug("Failed to unlink %s: %s", path, exc)


def _sweep_unmanaged_logs(
    log_dir: Path,
    *,
    managed_filename: str,
    aggregate_budget_mb: int,
    unmanaged_truncate_mb: int,
    unmanaged_max_age_days: int,
) -> None:
    """Cleanup ``logs/`` files we don't control via RotatingFileHandler.

    Three policies, applied in order:

    1. **Truncate huge unmanaged files** — if any ``*.log`` file (not the
       managed one) exceeds ``unmanaged_truncate_mb`` MB, truncate it to
       0 bytes. Catches things like ``backend-restart.log`` (script
       stdout redirect), ``openbiliclaw-restart.log``, etc. Truncation
       (not deletion) so live tail-ers don't lose their fd.
    2. **Delete stale unmanaged files** — anything older than
       ``unmanaged_max_age_days`` days gets removed entirely. Old
       one-shot logs from past install / debug sessions.
    3. **Cap aggregate dir size** — total bytes in ``logs/`` (managed +
       unmanaged) summed up. If over ``aggregate_budget_mb`` MB, delete
       oldest unmanaged files until under budget. Managed files are
       kept regardless (RotatingFileHandler is in charge of those).

    Each delete / truncate emits an INFO log so users see what got
    cleaned. All errors are swallowed — startup must not abort because
    of cleanup hiccups.
    """
    if not log_dir.exists() or not log_dir.is_dir():
        return

    entries = _log_file_entries(log_dir)
    if entries is None:
        return

    now = time.time()
    age_cutoff = now - unmanaged_max_age_days * 86400 if unmanaged_max_age_days > 0 else 0.0
    _truncate_unmanaged_logs(
        entries,
        managed_filename=managed_filename,
        threshold_mb=unmanaged_truncate_mb,
    )
    _delete_stale_unmanaged_logs(
        entries,
        managed_filename=managed_filename,
        age_cutoff=age_cutoff,
    )
    _enforce_log_budget(
        log_dir,
        managed_filename=managed_filename,
        aggregate_budget_mb=aggregate_budget_mb,
    )


def configure_logging(
    config: Config,
    console_level_override: str | None = None,
    *,
    sweep_unmanaged: bool = True,
) -> None:
    """Configure root logging for console and file output.

    ``sweep_unmanaged=False`` skips the v0.3.30+ ``logs/`` directory
    cleanup pass — used by the ``logs-prune`` CLI command which runs
    its own dry-run-aware cleanup and shouldn't be ambushed by the
    auto-sweep inside the global Typer callback.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    console_level = _coerce_level(console_level_override or config.logging.level)
    file_level = _coerce_level(config.logging.file_level)

    console_handler = RichHandler(rich_tracebacks=True, show_path=False)
    setattr(console_handler, _OWNED_SINK_ATTRIBUTE, "console")
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    log_file = config.logging.file_path
    log_file.parent.mkdir(parents=True, exist_ok=True)

    _enforce_size_budget_once(log_file, config.logging.max_file_size_mb)
    # v0.3.30+: also sweep unmanaged files in the same logs dir.
    # Catches stdout-redirect logs from start scripts, stale one-off
    # bootstrap logs, and the aggregate-size budget.
    if sweep_unmanaged:
        _sweep_unmanaged_logs(
            config.logging.directory_path,
            managed_filename=config.logging.filename,
            aggregate_budget_mb=config.logging.aggregate_budget_mb,
            unmanaged_truncate_mb=config.logging.unmanaged_truncate_mb,
            unmanaged_max_age_days=config.logging.unmanaged_max_age_days,
        )
    file_handler = _build_file_handler(
        log_file,
        max_file_size_mb=config.logging.max_file_size_mb,
        backup_count=config.logging.backup_count,
        level=file_level,
    )
    setattr(file_handler, _OWNED_SINK_ATTRIBUTE, "file")

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    for logger_name in _NOISY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
