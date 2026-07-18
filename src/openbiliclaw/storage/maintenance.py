"""SQLite maintenance helpers for integrity checks, backups, and repair."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

_BACKUP_NAME_PREFIX = "openbiliclaw-"
_BACKUP_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"
_RETENTION_DAYS = 7
_RETENTION_WEEKS = 4
_DEFAULT_BACKUP_INTERVAL = timedelta(hours=24)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


@dataclass(frozen=True)
class IntegrityReport:
    healthy: bool
    error: str = ""


@dataclass(frozen=True)
class BackupResult:
    db_backup: Path
    wal_backup: Path | None


@dataclass(frozen=True)
class RepairResult:
    status: str
    message: str
    repaired_db: Path | None
    db_backup: Path | None
    wal_backup: Path | None


def check_database_integrity(db_path: Path) -> IntegrityReport:
    """Return whether a SQLite database passes integrity check."""
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        return IntegrityReport(healthy=False, error=str(exc))
    finally:
        connection.close()

    if row is None:
        return IntegrityReport(healthy=False, error="integrity_check returned no rows")
    result = str(row[0]).strip()
    if result.lower() == "ok":
        return IntegrityReport(healthy=True, error="")
    return IntegrityReport(healthy=False, error=result)


def create_database_backup(
    db_path: Path,
    backup_dir: Path,
    *,
    timestamp: str | None = None,
) -> BackupResult:
    """Create a timestamped cold backup for the database and optional WAL."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or _timestamp_now()
    backup_db = backup_dir / f"{_backup_base_name(db_path, stamp)}.db"
    shutil.copy2(db_path, backup_db)

    wal_path = db_path.with_name(f"{db_path.name}-wal")
    backup_wal: Path | None = None
    if wal_path.exists():
        backup_wal = backup_dir / f"{_backup_base_name(db_path, stamp)}.db-wal"
        shutil.copy2(wal_path, backup_wal)

    return BackupResult(db_backup=backup_db, wal_backup=backup_wal)


def rotate_database_backups(
    backup_dir: Path,
    *,
    keep_daily: int = _RETENTION_DAYS,
    keep_weekly: int = _RETENTION_WEEKS,
    now: datetime | None = None,
) -> None:
    """Keep recent daily backups and one backup for each recent week."""
    if not backup_dir.exists():
        return

    backups = sorted(
        (_parse_backup_timestamp(path), path)
        for path in backup_dir.glob("*.db")
        if _parse_backup_timestamp(path) is not None
    )
    if not backups:
        return

    keep: set[Path] = set()
    weekly_groups: set[tuple[int, int]] = set()

    for stamp, path in sorted(backups, reverse=True):
        assert stamp is not None
        if len(keep) < keep_daily:
            keep.add(path)
            continue

        iso_year, iso_week, _ = stamp.isocalendar()
        week_key = (iso_year, iso_week)
        if len(weekly_groups) >= keep_weekly or week_key in weekly_groups:
            continue
        keep.add(path)
        weekly_groups.add(week_key)

    for _, path in backups:
        if path in keep:
            continue
        path.unlink(missing_ok=True)
        wal_path = path.with_suffix(".db-wal")
        wal_path.unlink(missing_ok=True)


def repair_database(
    db_path: Path,
    *,
    backup_dir: Path,
    holders: Sequence[str] | None = None,
    integrity_error: str | None = None,
    recovered_sql: str | None = None,
) -> RepairResult:
    """Repair a damaged database into a fresh file and replace atomically."""
    active_holders = list(holders) if holders is not None else list_database_holders(db_path)
    if active_holders:
        return RepairResult(
            status="in_use",
            message=f"数据库仍在被这些进程占用：{', '.join(active_holders)}",
            repaired_db=None,
            db_backup=None,
            wal_backup=None,
        )

    if integrity_error is None:
        report = check_database_integrity(db_path)
        if report.healthy:
            return RepairResult(
                status="healthy",
                message="数据库完整，无需修复。",
                repaired_db=None,
                db_backup=None,
                wal_backup=None,
            )
        integrity_error = report.error or "database integrity check failed"

    backup = create_database_backup(db_path, backup_dir)
    rotate_database_backups(backup_dir)

    recovered_sql_text = (
        recovered_sql if recovered_sql is not None else recover_database_sql(db_path)
    )
    if not recovered_sql_text:
        return RepairResult(
            status="failed",
            message=f"数据库修复失败：{integrity_error}",
            repaired_db=None,
            db_backup=backup.db_backup,
            wal_backup=backup.wal_backup,
        )

    repaired_db = db_path.with_suffix(".repaired.db")
    repaired_db.unlink(missing_ok=True)
    try:
        connection = sqlite3.connect(str(repaired_db))
        try:
            connection.executescript(recovered_sql_text)
            connection.commit()
        finally:
            connection.close()
        repaired_report = check_database_integrity(repaired_db)
        if not repaired_report.healthy:
            repaired_db.unlink(missing_ok=True)
            return RepairResult(
                status="failed",
                message=f"数据库修复失败：{repaired_report.error}",
                repaired_db=None,
                db_backup=backup.db_backup,
                wal_backup=backup.wal_backup,
            )
        original_backup = db_path.with_suffix(".broken.db")
        original_backup.unlink(missing_ok=True)
        db_path.replace(original_backup)
        shutil.copy2(repaired_db, db_path)
        db_path.with_name(f"{db_path.name}-wal").unlink(missing_ok=True)
        return RepairResult(
            status="repaired",
            message="数据库已恢复并完成切换。",
            repaired_db=repaired_db,
            db_backup=backup.db_backup,
            wal_backup=backup.wal_backup,
        )
    except sqlite3.DatabaseError as exc:
        repaired_db.unlink(missing_ok=True)
        return RepairResult(
            status="failed",
            message=f"数据库修复失败：{exc}",
            repaired_db=None,
            db_backup=backup.db_backup,
            wal_backup=backup.wal_backup,
        )


def list_database_holders(db_path: Path) -> list[str]:
    """Return processes currently holding the database or WAL file."""
    wal_path = db_path.with_name(f"{db_path.name}-wal")
    targets = [str(db_path)]
    if wal_path.exists():
        targets.append(str(wal_path))
    try:
        result = subprocess.run(
            ["lsof", *targets],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return []
    if result.returncode not in {0, 1}:
        return []
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) <= 1:
        return []

    holders: list[str] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        holders.append(f"{parts[0]}:{parts[1]}")
    return holders


def recover_database_sql(db_path: Path) -> str | None:
    """Try to recover SQL statements from a damaged SQLite database."""
    try:
        result = subprocess.run(
            ["sqlite3", str(db_path), ".recover"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    recovered = result.stdout.strip()
    return recovered or None


def maybe_create_scheduled_backup(
    db_path: Path,
    backup_dir: Path,
    *,
    now: datetime | None = None,
    minimum_interval: timedelta = _DEFAULT_BACKUP_INTERVAL,
) -> BackupResult | None:
    """Create a cold backup when enough time has elapsed and DB is healthy."""
    report = check_database_integrity(db_path)
    if not report.healthy:
        return None
    current = _to_utc(now) if now is not None else datetime.now(UTC)
    latest = latest_backup_timestamp(backup_dir)
    if latest is not None and current - latest < minimum_interval:
        return None
    backup = create_database_backup(
        db_path,
        backup_dir,
        timestamp=current.strftime(_BACKUP_TIMESTAMP_FORMAT),
    )
    rotate_database_backups(backup_dir, now=current)
    return backup


def latest_backup_timestamp(backup_dir: Path) -> datetime | None:
    """Return the newest timestamp among stored backups."""
    if not backup_dir.exists():
        return None
    stamps = [
        parsed
        for parsed in (_parse_backup_timestamp(path) for path in backup_dir.glob("*.db"))
        if parsed is not None
    ]
    return max(stamps) if stamps else None


def _backup_base_name(db_path: Path, timestamp: str) -> str:
    stem = db_path.stem or "openbiliclaw"
    return f"{stem}-{timestamp}"


def _parse_backup_timestamp(path: Path) -> datetime | None:
    if path.suffix != ".db":
        return None
    if not path.stem.startswith(_BACKUP_NAME_PREFIX):
        prefix = f"{path.stem.split('-', 1)[0]}-"
    else:
        prefix = _BACKUP_NAME_PREFIX
    stamp = path.stem.removeprefix(prefix)
    try:
        parsed = datetime.strptime(stamp, _BACKUP_TIMESTAMP_FORMAT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC)


def _timestamp_now() -> str:
    return datetime.now(UTC).strftime(_BACKUP_TIMESTAMP_FORMAT)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
