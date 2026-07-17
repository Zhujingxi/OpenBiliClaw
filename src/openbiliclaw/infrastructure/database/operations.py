"""Operational diagnostics and safe SQLite backups.

This adapter is the sole owner of operational SQL used by the CLI and runtime
startup gates. Product features continue to use repositories and units of work.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url


@dataclass(frozen=True, slots=True)
class RuntimeDatabaseHealth:
    """Secret-free health facts for the application and queue databases."""

    database_exists: bool
    database_reachable: bool
    database_integrity_ok: bool
    migration_at_head: bool
    queue_exists: bool
    queue_integrity_ok: bool
    queue_writable: bool
    paths_separate: bool

    @property
    def ready(self) -> bool:
        """Return whether every persistence prerequisite is healthy."""

        return all(
            (
                self.database_exists,
                self.database_reachable,
                self.database_integrity_ok,
                self.migration_at_head,
                self.queue_exists,
                self.queue_integrity_ok,
                self.queue_writable,
                self.paths_separate,
            )
        )


class DatabaseBackupError(RuntimeError):
    """A safe, user-facing backup failure without sensitive path internals."""


class SchemaNotReadyError(RuntimeError):
    """The application database is absent, invalid, or not at Alembic head."""


def require_schema_at_head(*, database_url: str, alembic_ini: Path) -> None:
    """Fail closed unless a file-backed SQLite database is already migrated.

    Runtime processes use this read-only check. Migration ownership stays with
    the installer or the one-shot Compose migration service, so concurrent API
    and worker startup can never compete for SQLite DDL locks.
    """

    database_path = _sqlite_path(database_url)
    if not (
        _regular_file(database_path)
        and _integrity_ok(database_path)
        and _migration_at_head(
            database_path=database_path,
            database_url=database_url,
            alembic_ini=alembic_ini,
        )
    ):
        raise SchemaNotReadyError(
            "vNext database schema is not at Alembic head; run openbiliclaw db migrate"
        )


@dataclass(frozen=True, slots=True)
class _StableSQLiteSource:
    """Pinned private link set used to preserve SQLite sidecar semantics."""

    directory: Path
    directory_descriptor: int | None
    directory_identity: tuple[int, int]
    source: Path
    database_identity: tuple[int, int]
    sidecar_descriptors: tuple[int, ...]
    sidecar_identities: tuple[tuple[Path, tuple[int, int] | None], ...]
    staging_entries: tuple[tuple[str, tuple[int, int]], ...]
    uri: str
    descriptor_backed: bool


@dataclass(frozen=True, slots=True)
class _DestinationDirectory:
    """Pinned destination directory used for relative no-follow operations."""

    path: Path
    descriptor: int | None
    identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _OwnedFile:
    """A file descriptor and the inode exclusively created by this invocation."""

    path: Path
    descriptor: int
    identity: tuple[int, int]


_SNAPSHOT_ATTEMPTS = 3
_COPY_BUFFER_SIZE = 1024 * 1024


class SQLiteOperationalStore:
    """Perform file-backed SQLite diagnostics and no-overwrite backups."""

    def diagnose(
        self,
        *,
        database_url: str,
        queue_path: Path,
        alembic_ini: Path,
    ) -> RuntimeDatabaseHealth:
        """Inspect both SQLite files and compare the app revision with Alembic head."""

        database_path = _sqlite_path(database_url)
        queue = queue_path.expanduser().absolute()
        database_exists = _regular_file(database_path)
        queue_exists = _regular_file(queue)
        database_integrity = database_exists and _integrity_ok(database_path)
        queue_integrity = queue_exists and _integrity_ok(queue)
        queue_writable = queue_integrity and _write_transaction_available(queue)
        return RuntimeDatabaseHealth(
            database_exists=database_exists,
            database_reachable=database_integrity,
            database_integrity_ok=database_integrity,
            migration_at_head=(
                database_integrity
                and _migration_at_head(
                    database_path=database_path,
                    database_url=database_url,
                    alembic_ini=alembic_ini,
                )
            ),
            queue_exists=queue_exists,
            queue_integrity_ok=queue_integrity,
            queue_writable=queue_writable,
            paths_separate=_paths_separate(database_path, queue),
        )

    def backup(self, *, source: Path, destination: Path) -> Path:
        """Publish a consistent private backup without ever replacing a path."""

        try:
            return self._backup(source=source, destination=destination)
        except DatabaseBackupError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseBackupError("database backup failed") from exc

    def _backup(self, *, source: Path, destination: Path) -> Path:
        source_path = source.expanduser().absolute()
        target = _prepare_destination(destination)
        if source_path == target:
            raise DatabaseBackupError("backup destination must differ from the database")

        source_descriptor, source_identity = _open_source(source_path)
        try:
            directory = _open_destination_directory(target.parent)
            try:
                return self._backup_in_directory(
                    source=source_path,
                    source_descriptor=source_descriptor,
                    source_identity=source_identity,
                    target=target,
                    directory=directory,
                )
            finally:
                _close_destination_directory(directory)
        finally:
            os.close(source_descriptor)

    def _backup_in_directory(
        self,
        *,
        source: Path,
        source_descriptor: int,
        source_identity: tuple[int, int],
        target: Path,
        directory: _DestinationDirectory,
    ) -> Path:
        temp_path, temp_descriptor, temp_identity = self._create_temp(directory)
        temp = _OwnedFile(temp_path, temp_descriptor, temp_identity)
        try:
            self._backup_into_descriptor(
                source=source,
                source_descriptor=source_descriptor,
                source_identity=source_identity,
                snapshot_parent=directory.path,
                descriptor=temp.descriptor,
            )
            _require_source_identity(source, source_identity)
            _require_owned_entry(directory, temp)
            _sync_descriptor(temp.descriptor, temp.identity)
            final = _reserve_destination(directory=directory, target=target)
            try:
                _require_source_identity(source, source_identity)
                _copy_descriptor(source=temp.descriptor, destination=final.descriptor)
                _sync_descriptor(final.descriptor, final.identity)
                _require_owned_entry(directory, final)
                _require_destination_directory(directory)
                _sync_directory(directory)
                _require_source_identity(source, source_identity)
                return target
            except BaseException:
                _unlink_owned_entry(directory, final)
                raise
            finally:
                os.close(final.descriptor)
        finally:
            try:
                os.close(temp.descriptor)
            finally:
                _unlink_owned_entry(directory, temp)

    def _create_temp(self, directory: _DestinationDirectory) -> tuple[Path, int, tuple[int, int]]:
        """Create and retain a private temporary inode in the destination filesystem."""

        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        for _attempt in range(32):
            temp = directory.path / f".backup-{secrets.token_hex(12)}.tmp"
            try:
                descriptor = (
                    os.open(temp, flags, 0o600)
                    if directory.descriptor is None
                    else os.open(temp.name, flags, 0o600, dir_fd=directory.descriptor)
                )
            except FileExistsError:
                continue
            metadata = os.fstat(descriptor)
            identity = (metadata.st_dev, metadata.st_ino)
            owned = _OwnedFile(temp, descriptor, identity)
            try:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
                return temp, descriptor, identity
            except BaseException:
                try:
                    os.close(descriptor)
                finally:
                    _unlink_owned_entry(directory, owned)
                raise
        raise DatabaseBackupError("could not reserve a backup temporary file")

    def _backup_into_descriptor(
        self,
        *,
        source: Path,
        source_descriptor: int,
        source_identity: tuple[int, int],
        snapshot_parent: Path,
        descriptor: int,
    ) -> None:
        """Build a consistent snapshot in memory, then write only to the held inode."""

        payload = _snapshot_with_retries(
            source=source,
            source_descriptor=source_descriptor,
            source_identity=source_identity,
            snapshot_parent=snapshot_parent,
        )
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise DatabaseBackupError("database backup failed while writing snapshot")
            view = view[written:]


def _snapshot_with_retries(
    *,
    source: Path,
    source_descriptor: int,
    source_identity: tuple[int, int],
    snapshot_parent: Path,
) -> bytes:
    last_churn: DatabaseBackupError | None = None
    for attempt in range(_SNAPSHOT_ATTEMPTS):
        try:
            return _snapshot_once(
                source=source,
                source_descriptor=source_descriptor,
                source_identity=source_identity,
                snapshot_parent=snapshot_parent,
            )
        except DatabaseBackupError as exc:
            if not _retryable_identity_churn(exc) or attempt + 1 == _SNAPSHOT_ATTEMPTS:
                raise
            last_churn = exc
        except sqlite3.Error:
            if last_churn is None:
                raise
            raise last_churn from None
    raise DatabaseBackupError("database source or sidecar changed during backup")


def _snapshot_once(
    *,
    source: Path,
    source_descriptor: int,
    source_identity: tuple[int, int],
    snapshot_parent: Path,
) -> bytes:
    _require_source_identity(source, source_identity)
    stable = _create_stable_source(
        source=source,
        source_descriptor=source_descriptor,
        source_identity=source_identity,
        snapshot_parent=snapshot_parent,
    )
    try:
        with (
            sqlite3.connect(stable.uri, uri=True) as source_db,
            sqlite3.connect(":memory:") as snapshot,
        ):
            _require_stable_source(stable)
            source_db.backup(snapshot)
            _require_stable_source(stable)
            result = snapshot.execute("PRAGMA integrity_check").fetchone()
            if result != ("ok",):
                raise DatabaseBackupError("database backup failed integrity verification")
            payload = snapshot.serialize()
        _require_stable_source(stable)
        return payload
    finally:
        _close_stable_source(stable)


def _retryable_identity_churn(error: DatabaseBackupError) -> bool:
    message = str(error)
    return message in {
        "database source changed during backup",
        "database sidecar changed during backup",
    }


def _sqlite_path(database_url: str) -> Path:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database:
        raise ValueError("operations require file-backed SQLite")
    if parsed.database == ":memory:":
        raise ValueError("operations require file-backed SQLite")
    return Path(parsed.database).expanduser().absolute()


def _regular_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode)


def _integrity_ok(path: Path) -> bool:
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            return bool(result == ("ok",))
    except sqlite3.Error:
        return False


def _write_transaction_available(path: Path) -> bool:
    """Check queue write access without committing a persistent mutation."""

    uri = f"file:{quote(str(path), safe='/')}?mode=rw"
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        with sqlite3.connect(uri, uri=True, timeout=1.0) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ROLLBACK")
        return True
    except (OSError, sqlite3.Error):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _migration_at_head(*, database_path: Path, database_url: str, alembic_ini: Path) -> bool:
    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", database_url)
    try:
        heads = set(ScriptDirectory.from_config(config).get_heads())
        uri = f"file:{quote(str(database_path), safe='/')}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except (OSError, sqlite3.Error):
        return False
    return bool(heads) and {str(row[0]) for row in rows} == heads


def _paths_separate(first: Path, second: Path) -> bool:
    if first == second:
        return False
    try:
        return not os.path.samefile(first, second)
    except OSError:
        return True


def _open_source(source: Path) -> tuple[int, tuple[int, int]]:
    """Open and pin the requested database inode without following symlinks."""

    try:
        metadata = source.lstat()
    except OSError as exc:
        raise DatabaseBackupError("configured vNext database does not exist") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise DatabaseBackupError("database source must not be a symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise DatabaseBackupError("configured vNext database is not a regular file")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise DatabaseBackupError("database source changed before it could be opened") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise DatabaseBackupError("configured vNext database is not a regular file")
        identity = (opened.st_dev, opened.st_ino)
        expected = (metadata.st_dev, metadata.st_ino)
        if identity != expected:
            raise DatabaseBackupError("database source changed before it could be opened")
        _require_source_identity(source, identity)
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _create_stable_source(
    *,
    source: Path,
    source_descriptor: int,
    source_identity: tuple[int, int],
    snapshot_parent: Path,
) -> _StableSQLiteSource:
    """Create private hard links for the database and its live sidecars."""

    last_error: OSError | None = None
    parents = tuple(dict.fromkeys((source.parent, snapshot_parent)))
    for parent in parents:
        try:
            directory = Path(tempfile.mkdtemp(prefix=".obc-backup-source-", dir=parent))
        except OSError as exc:
            last_error = exc
            continue
        try:
            return _populate_stable_source(
                directory=directory,
                source=source,
                source_descriptor=source_descriptor,
                source_identity=source_identity,
            )
        except OSError as exc:
            last_error = exc
    raise DatabaseBackupError("could not create a stable database backup source") from last_error


def _populate_stable_source(
    *,
    directory: Path,
    source: Path,
    source_descriptor: int,
    source_identity: tuple[int, int],
) -> _StableSQLiteSource:
    directory_descriptor: int | None = None
    directory_identity: tuple[int, int] | None = None
    sidecar_descriptors: list[int] = []
    sidecar_identities: list[tuple[Path, tuple[int, int] | None]] = []
    staging_entries: list[tuple[str, tuple[int, int]]] = []
    try:
        initial_metadata = directory.lstat()
        directory_identity = (initial_metadata.st_dev, initial_metadata.st_ino)
        directory_descriptor = _open_directory(directory)
        directory_metadata = (
            directory.lstat() if directory_descriptor is None else os.fstat(directory_descriptor)
        )
        opened_identity = (directory_metadata.st_dev, directory_metadata.st_ino)
        if opened_identity != directory_identity or _path_identity(directory) != directory_identity:
            raise DatabaseBackupError("backup staging directory changed during creation")
        os.chmod(directory, 0o700)
        _require_directory_identity(directory, directory_identity)
        stable_database = directory / "source.db"
        _link_verified_file(
            source=source,
            source_descriptor=source_descriptor,
            source_identity=source_identity,
            destination=stable_database,
        )
        staging_entries.append((stable_database.name, source_identity))
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = source.with_name(f"{source.name}{suffix}")
            pinned_sidecar = _link_optional_sidecar(
                source=sidecar,
                destination=directory / f"source.db{suffix}",
            )
            if pinned_sidecar is None:
                sidecar_identities.append((sidecar, None))
            else:
                sidecar_descriptor, sidecar_identity = pinned_sidecar
                sidecar_descriptors.append(sidecar_descriptor)
                sidecar_identities.append((sidecar, sidecar_identity))
                staging_entries.append((f"source.db{suffix}", sidecar_identity))
        uri, descriptor_backed = _stable_source_uri(
            directory=directory,
            directory_descriptor=directory_descriptor,
        )
        return _StableSQLiteSource(
            directory=directory,
            directory_descriptor=directory_descriptor,
            directory_identity=directory_identity,
            source=source,
            database_identity=source_identity,
            sidecar_descriptors=tuple(sidecar_descriptors),
            sidecar_identities=tuple(sidecar_identities),
            staging_entries=tuple(staging_entries),
            uri=uri,
            descriptor_backed=descriptor_backed,
        )
    except BaseException as exc:
        close_error: DatabaseBackupError | None = None
        try:
            _close_descriptors(sidecar_descriptors)
        except DatabaseBackupError as descriptor_error:
            close_error = descriptor_error
        try:
            if directory_identity is not None:
                _cleanup_staging_directory(
                    directory=directory,
                    directory_descriptor=directory_descriptor,
                    directory_identity=directory_identity,
                    entries=tuple(staging_entries),
                )
        except BaseException as cleanup_error:
            raise DatabaseBackupError("database backup staging cleanup failed") from cleanup_error
        finally:
            if directory_descriptor is not None:
                os.close(directory_descriptor)
        if close_error is not None:
            raise close_error from exc
        if isinstance(exc, DatabaseBackupError):
            raise exc
        raise


def _link_verified_file(
    *,
    source: Path,
    source_descriptor: int,
    source_identity: tuple[int, int],
    destination: Path,
) -> None:
    opened = os.fstat(source_descriptor)
    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != source_identity:
        raise DatabaseBackupError("database source changed during backup")
    os.link(source, destination, follow_symlinks=False)
    try:
        linked = destination.lstat()
        if stat.S_ISLNK(linked.st_mode) or (linked.st_dev, linked.st_ino) != source_identity:
            raise DatabaseBackupError("database source changed during backup")
        _require_source_identity(source, source_identity)
    except BaseException:
        _unlink_owned_inode(destination, source_identity)
        raise


def _link_optional_sidecar(
    *, source: Path, destination: Path
) -> tuple[int, tuple[int, int]] | None:
    try:
        metadata = source.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DatabaseBackupError("database sidecar changed during backup")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source, flags)
    linked_created = False
    try:
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino)
        if not stat.S_ISREG(opened.st_mode) or identity != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            raise DatabaseBackupError("database sidecar changed during backup")
        os.link(source, destination, follow_symlinks=False)
        linked_created = True
        linked_metadata = destination.lstat()
        current = source.lstat()
        if (
            stat.S_ISLNK(linked_metadata.st_mode)
            or (linked_metadata.st_dev, linked_metadata.st_ino) != identity
            or stat.S_ISLNK(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
        ):
            raise DatabaseBackupError("database sidecar changed during backup")
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        if linked_created:
            _unlink_owned_inode(destination, identity)
        raise


def _open_directory(directory: Path) -> int | None:
    if os.name == "nt":
        return None
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(directory, directory_flags)


def _stable_source_uri(*, directory: Path, directory_descriptor: int | None) -> tuple[str, bool]:
    if directory_descriptor is not None:
        for descriptor_root in (Path("/proc/self/fd"), Path("/dev/fd")):
            pinned_directory = descriptor_root / str(directory_descriptor)
            database = pinned_directory / "source.db"
            if database.is_file():
                return f"file:{quote(str(database), safe='/')}?mode=ro", True
    database = directory / "source.db"
    return f"file:{quote(str(database), safe='/')}?mode=ro", False


def _require_stable_source(stable: _StableSQLiteSource) -> None:
    _require_source_identity(stable.source, stable.database_identity)
    _require_sidecar_identities(stable.sidecar_identities)
    if not stable.descriptor_backed:
        _require_directory_identity(stable.directory, stable.directory_identity)
    metadata = (
        (stable.directory / "source.db").lstat()
        if stable.directory_descriptor is None
        else os.stat(
            "source.db",
            dir_fd=stable.directory_descriptor,
            follow_symlinks=False,
        )
    )
    if (
        stat.S_ISLNK(metadata.st_mode)
        or (
            metadata.st_dev,
            metadata.st_ino,
        )
        != stable.database_identity
    ):
        raise DatabaseBackupError("stable database source changed during backup")


def _require_sidecar_identities(
    sidecars: tuple[tuple[Path, tuple[int, int] | None], ...],
) -> None:
    for path, identity in sidecars:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if identity is None:
                continue
            raise DatabaseBackupError("database sidecar changed during backup") from None
        if identity is None:
            raise DatabaseBackupError("database sidecar changed during backup")
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise DatabaseBackupError("database sidecar changed during backup")
        if (metadata.st_dev, metadata.st_ino) != identity:
            raise DatabaseBackupError("database sidecar changed during backup")


def _require_directory_identity(directory: Path, identity: tuple[int, int]) -> None:
    metadata = directory.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise DatabaseBackupError("stable database source changed during backup")
    if (metadata.st_dev, metadata.st_ino) != identity:
        raise DatabaseBackupError("stable database source changed during backup")


def _close_stable_source(stable: _StableSQLiteSource) -> None:
    close_error: DatabaseBackupError | None = None
    try:
        _close_descriptors(stable.sidecar_descriptors)
    except DatabaseBackupError as exc:
        close_error = exc
    try:
        _cleanup_staging_directory(
            directory=stable.directory,
            directory_descriptor=stable.directory_descriptor,
            directory_identity=stable.directory_identity,
            entries=stable.staging_entries,
        )
    finally:
        if stable.directory_descriptor is not None:
            os.close(stable.directory_descriptor)
    if close_error is not None:
        raise close_error


def _close_descriptors(descriptors: tuple[int, ...] | list[int]) -> None:
    first_error: OSError | None = None
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError as exc:
            first_error = first_error or exc
    if first_error is not None:
        raise DatabaseBackupError("database backup descriptor cleanup failed") from first_error


def _cleanup_staging_directory(
    *,
    directory: Path,
    directory_descriptor: int | None,
    directory_identity: tuple[int, int],
    entries: tuple[tuple[str, tuple[int, int]], ...],
) -> None:
    try:
        _require_directory_identity(directory, directory_identity)
    except (DatabaseBackupError, OSError) as exc:
        raise DatabaseBackupError(
            "stable database source changed; backup staging directory changed during cleanup"
        ) from exc

    if directory_descriptor is not None:
        opened = os.fstat(directory_descriptor)
        if (opened.st_dev, opened.st_ino) != directory_identity:
            raise DatabaseBackupError("backup staging directory changed during cleanup")
    expected = dict(entries)
    names = set(os.listdir(directory_descriptor if directory_descriptor is not None else directory))
    if "source.db" in expected and "source.db" not in names:
        raise DatabaseBackupError("backup staging directory changed during cleanup")
    allowed_dynamic = {"source.db-wal", "source.db-shm", "source.db-journal"}
    if not names.issubset(set(expected) | allowed_dynamic):
        raise DatabaseBackupError("backup staging directory changed during cleanup")
    dynamic_entries = tuple(
        (name, _staging_entry_identity(directory, directory_descriptor, name))
        for name in names - set(expected)
    )
    owned_entries = (
        *(entry for entry in entries if entry[0] in names),
        *dynamic_entries,
    )
    for name, identity in owned_entries:
        _require_staging_entry(
            directory=directory,
            directory_descriptor=directory_descriptor,
            name=name,
            identity=identity,
        )
    for name, identity in owned_entries:
        _require_staging_entry(
            directory=directory,
            directory_descriptor=directory_descriptor,
            name=name,
            identity=identity,
        )
        if directory_descriptor is None:
            (directory / name).unlink()
        else:
            os.unlink(name, dir_fd=directory_descriptor)
    _require_directory_identity(directory, directory_identity)
    os.rmdir(directory)


def _staging_entry_identity(
    directory: Path, directory_descriptor: int | None, name: str
) -> tuple[int, int]:
    metadata = (
        (directory / name).lstat()
        if directory_descriptor is None
        else os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    )
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DatabaseBackupError("backup staging directory changed during cleanup")
    return metadata.st_dev, metadata.st_ino


def _require_staging_entry(
    *,
    directory: Path,
    directory_descriptor: int | None,
    name: str,
    identity: tuple[int, int],
) -> None:
    metadata = (
        (directory / name).lstat()
        if directory_descriptor is None
        else os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    )
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != identity
    ):
        raise DatabaseBackupError("backup staging directory changed during cleanup")


def _require_source_identity(source: Path, identity: tuple[int, int]) -> None:
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise DatabaseBackupError("database source changed during backup") from exc
    if stat.S_ISLNK(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != identity:
        raise DatabaseBackupError("database source changed during backup")


def _path_identity(path: Path) -> tuple[int, int]:
    metadata = path.lstat()
    return metadata.st_dev, metadata.st_ino


def _open_destination_directory(path: Path) -> _DestinationDirectory:
    identity = _path_identity(path)
    descriptor = _open_directory(path)
    if descriptor is not None:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != identity:
            os.close(descriptor)
            raise DatabaseBackupError("backup destination directory changed")
    directory = _DestinationDirectory(path=path, descriptor=descriptor, identity=identity)
    _require_destination_directory(directory)
    return directory


def _close_destination_directory(directory: _DestinationDirectory) -> None:
    if directory.descriptor is not None:
        os.close(directory.descriptor)


def _require_destination_directory(directory: _DestinationDirectory) -> None:
    try:
        metadata = directory.path.lstat()
    except OSError as exc:
        raise DatabaseBackupError("backup destination directory changed") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != directory.identity
    ):
        raise DatabaseBackupError("backup destination directory changed")


def _reserve_destination(*, directory: _DestinationDirectory, target: Path) -> _OwnedFile:
    _require_destination_directory(directory)
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = (
            os.open(target, flags, 0o600)
            if directory.descriptor is None
            else os.open(target.name, flags, 0o600, dir_fd=directory.descriptor)
        )
    except FileExistsError as exc:
        raise DatabaseBackupError("backup destination already exists") from exc
    metadata = os.fstat(descriptor)
    owned = _OwnedFile(target, descriptor, (metadata.st_dev, metadata.st_ino))
    try:
        _require_owned_entry(directory, owned)
        return owned
    except BaseException:
        try:
            os.close(descriptor)
        finally:
            _unlink_owned_entry(directory, owned)
        raise


def _copy_descriptor(*, source: int, destination: int) -> None:
    os.lseek(source, 0, os.SEEK_SET)
    os.ftruncate(destination, 0)
    while chunk := os.read(source, _COPY_BUFFER_SIZE):
        view = memoryview(chunk)
        while view:
            written = os.write(destination, view)
            if written <= 0:
                raise DatabaseBackupError("database backup failed while publishing snapshot")
            view = view[written:]


def _entry_metadata(directory: _DestinationDirectory, path: Path) -> os.stat_result:
    if directory.descriptor is None:
        return path.lstat()
    return os.stat(path.name, dir_fd=directory.descriptor, follow_symlinks=False)


def _require_owned_entry(directory: _DestinationDirectory, owned: _OwnedFile) -> None:
    message = (
        "backup temporary file changed"
        if owned.path.name.startswith(".backup-")
        else "backup destination changed"
    )
    try:
        metadata = _entry_metadata(directory, owned.path)
    except OSError as exc:
        raise DatabaseBackupError(message) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != owned.identity
    ):
        raise DatabaseBackupError(message)


def _unlink_owned_entry(directory: _DestinationDirectory, owned: _OwnedFile) -> None:
    _require_owned_entry(directory, owned)
    try:
        if directory.descriptor is None:
            owned.path.unlink()
        else:
            os.unlink(owned.path.name, dir_fd=directory.descriptor)
    except FileNotFoundError:
        return


def _prepare_destination(destination: Path) -> Path:
    expanded = destination.expanduser().absolute()
    expanded.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent = expanded.parent.resolve(strict=True)
    target = parent / expanded.name
    try:
        target.lstat()
    except FileNotFoundError:
        return target
    except OSError as exc:
        raise DatabaseBackupError("backup destination cannot be inspected") from exc
    raise DatabaseBackupError("backup destination already exists")


def _unlink_owned_inode(path: Path, identity: tuple[int, int]) -> None:
    try:
        metadata = path.lstat()
        if not stat.S_ISLNK(metadata.st_mode) and (metadata.st_dev, metadata.st_ino) == identity:
            path.unlink()
    except FileNotFoundError:
        return


def _sync_descriptor(descriptor: int, identity: tuple[int, int]) -> None:
    metadata = os.fstat(descriptor)
    if (metadata.st_dev, metadata.st_ino) != identity:
        raise DatabaseBackupError("backup temporary file changed during creation")
    os.fchmod(descriptor, 0o600)
    os.fsync(descriptor)


def _sync_directory(directory: _DestinationDirectory) -> None:
    if directory.descriptor is not None:
        os.fsync(directory.descriptor)
        return
    descriptor = os.open(directory.path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "DatabaseBackupError",
    "RuntimeDatabaseHealth",
    "SQLiteOperationalStore",
    "SchemaNotReadyError",
    "require_schema_at_head",
]
